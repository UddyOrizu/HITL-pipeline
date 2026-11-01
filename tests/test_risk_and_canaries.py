"""Milestone 5: risk-pointing, canaries, catch scoring, reveal, and the release hash check."""

import random
from pathlib import Path

from sqlalchemy import select

from tests.conftest import ack_all_flags, auth, make_tenant, read_fully, review, submit_text, task_for


def canary_row(sid):
    from db.models import Canary
    from db.session import session_scope

    with session_scope() as db:
        c = db.scalars(select(Canary).where(Canary.submission_id == sid)).first()
        return None if c is None else {k: getattr(c, k) for k in ("id", "span_id", "target_span_id", "type", "injected_text", "caught")}


def open_task(client, world, sid):
    who, item = task_for(client, world, sid)
    token = world["users"][who]["token"]
    return who, token, item["task_id"], client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(token)).json()


def all_span_text(view):
    return {sp["id"]: sp["text"] for s in view["document"]["sections"] for sp in s["spans"]}


def test_heuristic_risk_flags(client):
    world = make_tenant()
    text = ("Revenue rose to £4.2m in 2025.\n\n"
            "We recommend the client relies on this to meet its FCA regulatory obligations.\n\n"
            "According to the Hartley Review, margins may possibly improve.\n\n"
            "Thank you for reading this short note today.")
    sub = submit_text(client, world, text=text)
    _, _, _, view = open_task(client, world, sub["id"])
    spans = list(all_span_text(view).values())
    by_span = {}
    for f in view["flags"]:
        by_span.setdefault(next(t for i, t in all_span_text(view).items() if i == f["span_id"]), set()).add(f["category"])
    assert "unsupported_figure" in by_span[spans[0]]
    assert "regulated_advice" in by_span[spans[1]]
    assert {"unresolved_citation", "low_confidence"} <= by_span[spans[2]]
    assert spans[3] not in by_span
    assert all(f["mandatory"] == (f["severity"] == "high") for f in view["flags"])


def test_reference_documents_enable_claim_to_source_checks(client):
    world = make_tenant()
    sub = submit_text(client, world, text="The loan is £245,000.\n\nThe income is £61,000 per year.",
                      reference_texts=["Application: loan 245,000. Declared income 58,400."])
    _, _, _, view = open_task(client, world, sub["id"])
    texts = all_span_text(view)
    mismatch = [texts[f["span_id"]] for f in view["flags"] if f["category"] == "reference_mismatch"]
    assert mismatch == ["The income is £61,000 per year."]


def test_llm_path_goes_through_gateway_and_agents(client, monkeypatch):
    from agents import claim_extractor, risk_flagger
    from agents.schemas import Claim, ClaimList, RiskFlagList, RiskFlagOut
    from services import model_gateway

    model_gateway.set_gateway(model_gateway.ModelGateway("anthropic", "claude-sonnet-5-5"))
    seen = {}

    def fake_claims(gw, spans):
        seen["spans"] = spans
        return ClaimList(claims=[Claim(span_id=spans[0]["span_id"], text=spans[0]["text"], tags=["advice"], confidence=0.4)])

    def fake_flags(gw, claims, ref, tier):
        c = claims.claims[0]
        return RiskFlagList(flags=[RiskFlagOut(span_id=c.span_id, category="regulated_advice", severity="high", rationale="LLM says so"),
                                   RiskFlagOut(span_id="hallucinated-span", category="novel_claim", severity="low", rationale="x")])

    monkeypatch.setattr(claim_extractor, "run", fake_claims)
    monkeypatch.setattr(risk_flagger, "run", fake_flags)
    world = make_tenant()
    sub = submit_text(client, world, text="You should move all savings into one fund.\n\nSecond paragraph here today.")
    _, _, _, view = open_task(client, world, sub["id"])
    assert [f["rationale"] for f in view["flags"]] == ["LLM says so"]  # unknown span ids are dropped
    assert len(seen["spans"]) == 2

    from db.models import AuditEvent
    from db.session import session_scope

    with session_scope() as db:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "risk.flagged")).first()
        assert ev.payload["engine"] == "llm:anthropic"


def test_llm_failure_falls_back_to_heuristic(client, monkeypatch):
    from services import model_gateway

    gw = model_gateway.ModelGateway("anthropic", "claude-sonnet-5-5")

    class Boom:
        def run(self, *_):
            raise RuntimeError("provider down")

    monkeypatch.setattr("agents.claim_extractor.build", lambda g: Boom())
    model_gateway.set_gateway(gw)
    world = make_tenant()
    sub = submit_text(client, world)
    _, _, _, view = open_task(client, world, sub["id"])
    assert view["flags"]
    assert gw.calls and gw.calls[0].ok is False and gw.calls[0].agent == "claim_extractor"


def test_no_provider_sdk_calls_outside_the_gateway():
    root = Path(__file__).resolve().parent.parent
    banned = ("import anthropic", "from anthropic", "import openai", "from openai", "agno.models")
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if rel.parts[0] in (".venv", "tests", "web") or str(rel) == "services/model_gateway.py":
            continue
        source = path.read_text()
        for b in banned:
            assert b not in source, f"{rel} calls a provider SDK directly ({b})"


def test_canary_is_injected_only_into_the_review_copy(client):
    world = make_tenant(canary_rate=1.0, stages=[{"name": "One", "sla_hours": 24}, {"name": "Two", "sla_hours": 24}])
    sub = submit_text(client, world)
    c = canary_row(sub["id"])
    assert c is not None
    _, token, task_id, view = open_task(client, world, sub["id"])
    texts = all_span_text(view)
    assert texts[c["span_id"]] == c["injected_text"]
    if c["type"] == "factual_error":
        assert c["target_span_id"] not in texts  # the original sentence is replaced in the review copy
    # The content hash covers only the original.
    from db.models import Document, Submission
    from db.session import session_scope
    from services import ingestion

    with session_scope() as db:
        s = db.get(Submission, sub["id"])
        doc = db.scalars(select(Document).where(Document.submission_id == s.id)).one()
        assert ingestion.content_hash(db, doc.id) == s.content_hash
    # Stage two never sees the canary.
    review(client, world, sub["id"], "approve")
    _, _, _, view2 = open_task(client, world, sub["id"])
    assert c["span_id"] not in all_span_text(view2)


def test_canary_caught_by_comment_is_revealed(client):
    world = make_tenant(canary_rate=1.0)
    sub = submit_text(client, world)
    c = canary_row(sub["id"])
    _, token, task_id, view = open_task(client, world, sub["id"])
    client.post(f"/v1/review/tasks/{task_id}/comments", json={"span_id": c["span_id"], "body": "This looks wrong"}, headers=auth(token))
    read_fully(client, token, task_id, view)
    ack_all_flags(client, token, task_id, view)
    r = client.post(f"/v1/review/tasks/{task_id}/decision", json={"outcome": "approve", "confirm_warnings": True}, headers=auth(token)).json()
    assert r["canary"][0]["caught"] is True and "Well spotted" in r["canary"][0]["message"]
    assert canary_row(sub["id"])["caught"] is True


def test_missed_canary_is_revealed_as_calibration(client):
    world = make_tenant(canary_rate=1.0)
    sub = submit_text(client, world)
    _, result = review(client, world, sub["id"], "approve")
    assert result["canary"][0]["caught"] is False
    assert result["canary"][0]["canary_text"] == canary_row(sub["id"])["injected_text"]
    assert canary_row(sub["id"])["caught"] is False


def test_rejecting_only_the_canary_asks_for_a_decision_on_genuine_content(client, sink):
    world = make_tenant(canary_rate=1.0)
    sub = submit_text(client, world)
    c = canary_row(sub["id"])
    _, token, task_id, view = open_task(client, world, sub["id"])
    r = client.post(f"/v1/review/tasks/{task_id}/decision",
                    json={"outcome": "reject", "reason": "Contains an error", "cited_span_ids": [c["span_id"]]}, headers=auth(token))
    body = r.json()
    assert body["status"] == "canary_caught" and body["canary"][0]["caught"] is True
    view = client.get(f"/v1/review/tasks/{task_id}", headers=auth(token)).json()
    assert c["span_id"] not in all_span_text(view)  # canary removed, original restored
    read_fully(client, token, task_id, view)
    ack_all_flags(client, token, task_id, view)
    r = client.post(f"/v1/review/tasks/{task_id}/decision", json={"outcome": "approve", "confirm_warnings": True}, headers=auth(token))
    assert r.json()["status"] == "recorded" and r.json()["canary"] == []
    final = client.get(f"/v1/submissions/{sub['id']}", headers={"X-API-Key": world["api_key"]}).json()
    assert final["status"] == "approved"


def test_canary_types_and_templates_generate():
    from services import canary
    from services.canary_templates import TYPES

    canary.set_rng(random.Random(3))
    altered = canary._alter_number("Claims rose 18% to 12,450 in 2025.")
    assert altered and altered[0] != "Claims rose 18% to 12,450 in 2025."
    assert "{" not in canary._pick(canary.T.FABRICATED_CITATION, "financial")
    assert set(TYPES) == {"factual_error", "internal_contradiction", "fabricated_citation", "policy_breach", "absurd_statement"}


def test_release_check_blocks_tampered_content(client, sink):
    world = make_tenant()
    sub = submit_text(client, world)
    from db.models import Document, Span
    from db.session import session_scope

    with session_scope() as db:
        doc = db.scalars(select(Document).where(Document.submission_id == sub["id"])).one()
        span = db.scalars(select(Span).where(Span.document_id == doc.id)).first()
        span.text = span.text + " (altered)"
    review(client, world, sub["id"], "approve")
    s = client.get(f"/v1/submissions/{sub['id']}", headers={"X-API-Key": world["api_key"]}).json()
    assert s["status"] == "in_review" and s["decision"] is None and s["escalated"] is True
    assert not [e for e in sink.events() if e["event"] == "submission.decided"]
    lead = auth(world["users"]["lead"]["token"])
    held = client.get("/v1/oversight/submissions?held=true", headers=lead).json()
    assert held[0]["hold_reason"] == "release_check_failed"


def test_agno_agents_build_with_gateway_models():
    from agents import canary_generator, claim_extractor, risk_flagger
    from agents.schemas import CanaryOut, ClaimList, RiskFlagList
    from services.model_gateway import ModelGateway, load_prompt

    gw = ModelGateway("anthropic", "claude-sonnet-5-5", api_key="test-key")
    for module, schema in ((claim_extractor, ClaimList), (risk_flagger, RiskFlagList), (canary_generator, CanaryOut)):
        agent = module.build(gw)
        assert agent.output_schema is schema
        assert agent.model.id == "claude-sonnet-5-5"
        assert load_prompt(module.NAME) in str(agent.instructions)
