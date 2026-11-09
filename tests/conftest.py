from __future__ import annotations

import json
import os
import random
from datetime import UTC, datetime, timedelta

import httpx
import pytest

os.environ["POR_ENV_FILE"] = ""  # never read a developer's .env during tests
os.environ.setdefault("POR_ENV", "test")
os.environ.setdefault("POR_INLINE_JOBS", "true")
os.environ.setdefault("POR_MODEL_PROVIDER", "heuristic")


PG_URL = os.environ.get("POR_TEST_DATABASE_URL")  # e.g. postgresql+psycopg://por:por@localhost:5432/por_test


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("POR_DATABASE_URL", PG_URL or f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("POR_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("POR_PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("POR_MAIL_DIR", str(tmp_path / "mail"))
    from services.config import get_settings

    get_settings.cache_clear()
    from db.models import Base
    from db.session import reset_engine
    from services import canary, mailer, model_gateway, ratelimit, sso, storage, webhooks, workflow

    engine = reset_engine()
    if PG_URL:
        # Real Postgres: rebuild through the migrations so row-level security and triggers are live.
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import text

        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        command.upgrade(Config("alembic.ini"), "head")
    else:
        Base.metadata.create_all(engine)
    storage.reset_storage()
    model_gateway.set_gateway(None)
    canary.set_rng(random.Random(7))
    workflow.set_rng(random.Random(11))
    webhooks.set_transport(None)
    sso.set_transport(None)
    ratelimit.reset()
    mailer.outbox().clear()
    yield tmp_path
    engine.dispose()
    get_settings.cache_clear()


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)


class WebhookSink:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.fail_next = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_next:
            self.fail_next -= 1
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={"ok": True})

    def events(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture()
def sink(env):
    from services import webhooks

    s = WebhookSink()
    webhooks.set_transport(httpx.MockTransport(s.handler))
    return s


def make_tenant(name: str = "Acme", canary_rate: float = 0.0, spot_check_rate: float = 0.0, stages=None,
                assurance_policy=None, telemetry_policy=None, cap_policy=None, webhook_url="https://hooks.example.test/por",
                reviewers: int = 3) -> dict:
    from db.models import SourceApp, Team, Tenant, User, Workflow, new_id
    from db.session import session_scope
    from services.security import generate_api_key, generate_secret, issue_token

    with session_scope() as db:
        tenant = Tenant(name=name)
        db.add(tenant)
        db.flush()
        team = Team(tenant_id=tenant.id, name="Team A")
        db.add(team)
        db.flush()
        users = {}
        specs = [(f"r{i}@{name.lower()}.test", ["reviewer"]) for i in range(1, reviewers + 1)] + [
            (f"lead@{name.lower()}.test", ["oversight_lead"]),
            (f"auditor@{name.lower()}.test", ["auditor"]),
            (f"owner@{name.lower()}.test", ["integration_owner"]),
            (f"sub@{name.lower()}.test", ["submitter"]),
            (f"admin@{name.lower()}.test", ["admin"]),
        ]
        for email, roles in specs:
            u = User(tenant_id=tenant.id, email=email, name=email.split("@")[0], roles=roles, team_ids=[team.id])
            db.add(u)
            db.flush()
            users[email.split("@")[0]] = {"id": u.id, "token": issue_token(u.id, tenant.id, "user", roles)}
        key, prefix, key_hash = generate_api_key()
        secret = generate_secret()
        app = SourceApp(tenant_id=tenant.id, name=f"{name} app", api_key_prefix=prefix, api_key_hash=key_hash,
                        webhook_url=webhook_url, webhook_secret=secret)
        db.add(app)
        wf = Workflow(
            tenant_id=tenant.id, family_id=new_id(), version=1, name="Default",
            stages=stages or [{"name": "Review", "team_id": team.id, "sla_hours": 24}],
            canary_rate=canary_rate, spot_check_rate=spot_check_rate,
            telemetry_policy=telemetry_policy or {"min_reading_ratio": 0.3, "min_coverage": 0.6, "action": "warn"},
            cap_policy=cap_policy or {}, risk_policy={"mandatory_severities": ["high"]},
            assurance_policy=assurance_policy or {"min_score": 0, "below_action": "release_flag"},
        )
        db.add(wf)
        db.flush()
        return {"tenant_id": tenant.id, "team_id": team.id, "users": users, "api_key": key, "app_id": app.id,
                "webhook_secret": secret, "workflow_id": wf.family_id}


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def submit_text(client, world, text=None, **fields) -> dict:
    from scripts.sample_files import plain_text

    body = {"workflow_id": world["workflow_id"], "text": text or plain_text(), "external_ref": "EXT-1", **fields}
    r = client.post("/v1/submissions", json=body, headers={"X-API-Key": world["api_key"]})
    assert r.status_code in (200, 201), r.text
    return r.json()


def task_for(client, world, submission_id: str, kind: str | None = None) -> tuple[str, dict]:
    """Find which reviewer holds the open task for a submission. Returns (user_key, queue_item)."""
    for key, u in world["users"].items():
        if not key.startswith("r"):
            continue
        r = client.get("/v1/review/queue", headers=auth(u["token"]))
        assert r.status_code == 200, r.text
        for item in r.json():
            if item["submission_id"] == submission_id and (kind is None or item["kind"] == kind):
                return key, item
    raise AssertionError(f"no open task for {submission_id}")


def read_fully(client, token: str, task_id: str, view: dict, seconds: int = 50) -> None:
    """Simulate a reviewer reading every section, with heartbeats spread over `seconds`."""
    now = datetime.now(UTC).replace(tzinfo=None)
    start = now - timedelta(seconds=seconds)
    events = [{"type": "document_opened", "client_ts": start.isoformat()}]
    sections = view["document"]["sections"]
    for i in range(0, seconds, 5):
        events.append({"type": "heartbeat", "client_ts": (start + timedelta(seconds=i)).isoformat()})
    for j, s in enumerate(sections):
        events.append({"type": "section_in_view", "section_id": s["id"], "value": 4,
                       "client_ts": (start + timedelta(seconds=min(seconds - 1, j * 2 + 1))).isoformat()})
    events.append({"type": "scroll_depth", "value": 1.0, "client_ts": now.isoformat()})
    r = client.post("/v1/telemetry", json={"task_id": task_id, "events": events}, headers=auth(token))
    assert r.status_code == 202, r.text


def ack_all_flags(client, token: str, task_id: str, view: dict) -> None:
    for f in view["flags"]:
        r = client.post(f"/v1/review/tasks/{task_id}/flags/{f['id']}/acknowledge", headers=auth(token))
        assert r.status_code == 200, r.text


def review(client, world, submission_id: str, outcome: str = "approve", kind: str | None = None, reason: str = "",
           cited=None, read=True) -> tuple[str, dict]:
    who, item = task_for(client, world, submission_id, kind)
    token = world["users"][who]["token"]
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(token)).json()
    if read:
        read_fully(client, token, item["task_id"], view)
    ack_all_flags(client, token, item["task_id"], view)
    r = client.post(f"/v1/review/tasks/{item['task_id']}/decision",
                    json={"outcome": outcome, "reason": reason or ("Looks right" if outcome == "approve" else "Needs work"),
                          "cited_span_ids": cited or [], "confirm_warnings": True},
                    headers=auth(token))
    assert r.status_code == 200, r.text
    return who, r.json()
