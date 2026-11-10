"""Model usage recording, cost breakdown and operational reports."""

from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import select

from tests.conftest import auth, make_tenant, review, submit_text


class UsageGateway:
    """An enabled gateway whose agents 'fail' (so the heuristic engine runs) but report token usage."""

    def __new__(cls):
        from services.model_gateway import ModelGateway, Usage

        class _G(ModelGateway):
            def run_agent(self, agent, agent_name, user_input, output_schema):
                import time

                self._log(agent_name, False, time.monotonic(), len(user_input), "offline in tests",
                          usage=Usage(input_tokens=1000, output_tokens=200, cache_read_tokens=500))
                return None

            def agno_model(self):
                return None

        return _G("anthropic", "claude-sonnet-5-5")


@pytest.fixture()
def usage_gateway(env, monkeypatch):
    from agents import claim_extractor, risk_flagger
    from services import model_gateway

    gw = UsageGateway()
    model_gateway.set_gateway(gw)
    monkeypatch.setattr(claim_extractor, "build", lambda g: None)
    monkeypatch.setattr(risk_flagger, "build", lambda g: None)
    yield gw
    model_gateway.set_gateway(None)


def test_model_usage_is_recorded_per_submission(client, sink, usage_gateway):
    world = make_tenant()
    sub = submit_text(client, world)
    from db.models import ModelCall
    from db.session import session_scope

    with session_scope() as db:
        calls = db.scalars(select(ModelCall).where(ModelCall.submission_id == sub["id"])).all()
        assert calls and all(c.tenant_id == world["tenant_id"] and c.input_tokens == 1000 for c in calls)
        assert calls[0].model == "claude-sonnet-5-5" and calls[0].provider == "anthropic"


def test_cost_breakdown_prices_tokens_and_reviewer_time(client, sink, usage_gateway):
    world = make_tenant()
    admin = auth(world["users"]["admin"]["token"])
    lead = auth(world["users"]["lead"]["token"])
    client.put("/v1/admin/tenant", headers=admin, json={"default_hourly_rate": 60})
    client.put(f"/v1/admin/users/{world['users']['r1']['id']}", headers=admin, json={"hourly_rate": 120})
    sub = submit_text(client, world, external_ref="COST-1")
    who, _ = review(client, world, sub["id"], "approve")  # read_fully simulates 50 seconds of active reading

    from db.models import ModelCall
    from db.session import session_scope

    with session_scope() as db:
        n_calls = len(db.scalars(select(ModelCall).where(ModelCall.submission_id == sub["id"])).all())
    per_call = (1000 * 2.0 + 200 * 10.0 + 500 * 0.20) / 1_000_000  # claude-sonnet-5-5 list prices

    r = client.get("/v1/reports/costs?group_by=source_app", headers=lead)
    assert r.status_code == 200, r.text
    data = r.json()
    total = data["total"]
    assert total["model_calls"] == n_calls
    assert total["model_cost"] == pytest.approx(n_calls * per_call, abs=1e-4)
    assert total["tasks"] == 1 and total["review_minutes"] == pytest.approx(50 / 60, abs=0.01)
    rate = 120 if who == "r1" else 60  # r1 has their own rate; everyone else uses the organisation default
    assert total["labour_cost"] == pytest.approx(50 / 3600 * rate, abs=0.01)
    assert data["per_submission"] == pytest.approx(total["total_cost"], abs=1e-3)
    assert data["rows"][0]["label"] == "Acme app"

    by_model = {row["key"]: row for row in client.get("/v1/reports/costs?group_by=model", headers=lead).json()["rows"]}
    assert "claude-sonnet-5-5" in by_model and "review" in by_model
    by_reviewer = client.get("/v1/reports/costs?group_by=reviewer", headers=lead).json()["rows"]
    assert any(row["tasks"] == 1 for row in by_reviewer)

    # Tenant price overrides apply, and currency conversion is applied to model spend.
    client.put("/v1/admin/tenant", headers=admin, json={
        "currency": "GBP", "usd_to_currency": 0.5, "model_prices": {"claude-sonnet-5-5": {"input": 1, "output": 1}}})
    converted = client.get("/v1/reports/costs", headers=lead).json()
    # Cache reads default to 10% of the input price when the override does not give one.
    expected_usd = (1000 * 1 + 200 * 1 + 500 * 0.1) / 1e6 * n_calls
    assert converted["currency"] == "GBP"
    assert converted["total"]["model_cost_usd"] == pytest.approx(expected_usd, abs=1e-6)
    assert converted["total"]["model_cost"] == pytest.approx(expected_usd * 0.5, abs=1e-6)

    csv_text = client.get("/v1/reports/costs?group_by=day&format=csv", headers=lead).text
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert rows[-1]["label"] == "Total" and "labour_cost" in rows[0]
    # Reviewers and auditors cannot see costs.
    assert client.get("/v1/reports/costs", headers=auth(world["users"]["r1"]["token"])).status_code == 403
    assert client.get("/v1/reports/costs", headers=auth(world["users"]["auditor"]["token"])).status_code == 403


def test_unpriced_models_are_reported(client, env):
    world = make_tenant()
    from db.models import ModelCall
    from db.session import session_scope

    with session_scope() as db:
        db.add(ModelCall(tenant_id=world["tenant_id"], agent="x", provider="openai", model="gpt-something", ok=True,
                         latency_ms=1, input_tokens=100, output_tokens=10, cache_read_tokens=0, cache_write_tokens=0))
    data = client.get("/v1/reports/costs?group_by=model", headers=auth(world["users"]["lead"]["token"])).json()
    assert data["unpriced_models"] == [{"model": "gpt-something", "tokens": 110}]


def test_price_lookup_handles_platform_ids():
    from services.costs import DEFAULT_MODEL_PRICES, price_for

    assert price_for(DEFAULT_MODEL_PRICES, "claude-opus-5-5")["input"] == 4.0
    assert price_for(DEFAULT_MODEL_PRICES, "anthropic.claude-opus-5-5")["input"] == 4.0
    assert price_for(DEFAULT_MODEL_PRICES, "claude-opus-5")["input"] == 5.0
    assert price_for(DEFAULT_MODEL_PRICES, "unknown-model") is None


def test_overview_report(client, sink):
    world = make_tenant()
    a = submit_text(client, world, external_ref="R-1")
    b = submit_text(client, world, external_ref="R-2", risk_tier="high")
    submit_text(client, world, external_ref="R-3")
    review(client, world, a["id"], "approve")
    review(client, world, b["id"], "reject", reason="Wrong figures")
    lead = auth(world["users"]["lead"]["token"])
    r = client.get("/v1/reports/overview?interval=day", headers=lead)
    assert r.status_code == 200, r.text
    k = r.json()["kpis"]
    assert k["received"] == 3 and k["decided"] == 2 and k["approved"] == 1 and k["rejected"] == 1
    assert k["approval_rate"] == 0.5 and k["in_progress"] == 1 and k["median_turnaround_hours"] is not None
    series = r.json()["series"]
    assert sum(p["received"] for p in series) == 3 and sum(p["decided"] for p in series) == 2
    tiers = {row["key"]: row for row in r.json()["by_risk_tier"]}
    assert tiers["high"]["received"] == 1
    assert r.json()["by_channel"] == [{"channel": "api", "decisions": 2}]
    # Auditors can read the overview; reviewers cannot.
    assert client.get("/v1/reports/overview", headers=auth(world["users"]["auditor"]["token"])).status_code == 200
    assert client.get("/v1/reports/overview", headers=auth(world["users"]["r1"]["token"])).status_code == 403
    assert client.get("/v1/reports/overview?from=2026-02-01&to=2026-01-01", headers=lead).status_code == 422
    csv_text = client.get("/v1/reports/overview?format=csv", headers=lead).text
    assert csv_text.startswith("section,key,label,period,received,decided")
    sections = {line.split(",", 1)[0] for line in csv_text.strip().splitlines()[1:]}
    assert {"kpi", "series", "by_workflow", "by_source_app", "by_risk_tier", "by_origin", "by_channel"} <= sections


def test_reviewer_report_and_sla(client, sink):
    world = make_tenant(canary_rate=1.0)
    sub = submit_text(client, world)
    review(client, world, sub["id"], "approve")
    lead = auth(world["users"]["lead"]["token"])
    rows = client.get("/v1/reports/reviewers", headers=lead).json()["rows"]
    active = [row for row in rows if row["tasks"]]
    assert len(active) == 1 and active[0]["canaries_scored"] == 1 and active[0]["catch_rate"] == 0.0
    assert {row["name"] for row in rows} >= {"r1", "r2", "r3"}
    assert client.get("/v1/reports/sla", headers=lead).json() == {"overdue": []}
    csv_text = client.get("/v1/reports/reviewers?format=csv", headers=lead).text
    assert csv_text.splitlines()[0].startswith("name,email,tasks")


def test_csv_cells_cannot_start_formulas():
    from services.costs import to_csv

    out = to_csv([{"label": "=HYPERLINK(\"x\")", "key": "+1"}], ["key", "label"])
    assert "'=HYPERLINK" in out and "'+1" in out
