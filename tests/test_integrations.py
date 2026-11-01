"""Milestone 8: signed webhooks with retries, MCP server tools, and the SharePoint, Teams and Jira connectors."""

import base64
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import auth, make_tenant, review, submit_text, task_for


def deliveries(sid=None):
    from db.models import WebhookDelivery
    from db.session import session_scope

    with session_scope() as db:
        q = select(WebhookDelivery).order_by(WebhookDelivery.created_at)
        if sid:
            q = q.where(WebhookDelivery.submission_id == sid)
        return [{k: getattr(d, k) for k in ("id", "event", "status", "attempts", "last_status_code")} for d in db.scalars(q).all()]


def test_webhook_signature_and_tamper_detection(client, sink):
    world = make_tenant()
    submit_text(client, world)
    from services.webhooks import verify

    req = sink.requests[0]
    assert req.headers["X-PoR-Event"] == "submission.received"
    assert verify(world["webhook_secret"], req.headers["X-PoR-Signature"], req.content)
    assert not verify(world["webhook_secret"], req.headers["X-PoR-Signature"], req.content + b" ")
    assert not verify("other-secret", req.headers["X-PoR-Signature"], req.content)


def test_webhook_retries_with_backoff_then_delivers(client, sink):
    sink.fail_next = 1
    world = make_tenant()
    sub = submit_text(client, world)
    first = deliveries(sub["id"])[0]
    assert first["event"] == "submission.received" and first["status"] == "pending" and first["attempts"] == 1
    assert first["last_status_code"] == 503

    from db.models import WebhookDelivery, utcnow
    from db.session import session_scope
    from services import webhooks

    assert webhooks.backoff_seconds(1) == 30 and webhooks.backoff_seconds(3) == 120 and webhooks.backoff_seconds(20) == 3600
    assert webhooks.retry_due() == 0  # not due yet
    with session_scope() as db:
        db.get(WebhookDelivery, first["id"]).next_attempt_at = utcnow() - timedelta(seconds=1)
    assert webhooks.retry_due() == 1
    d = deliveries(sub["id"])[0]
    assert d["status"] == "delivered" and d["attempts"] == 2


def test_webhook_gives_up_after_24_hours(client, sink):
    sink.fail_next = 100
    world = make_tenant()
    sub = submit_text(client, world)
    from db.models import WebhookDelivery, utcnow
    from db.session import session_scope
    from services import webhooks

    with session_scope() as db:
        d = db.scalars(select(WebhookDelivery).where(WebhookDelivery.submission_id == sub["id"])).first()
        d.created_at = utcnow() - timedelta(hours=23, minutes=59, seconds=50)
        d.next_attempt_at = utcnow() - timedelta(seconds=1)
        did = d.id
    webhooks.retry_due()
    assert next(x for x in deliveries() if x["id"] == did)["status"] == "failed"

    owner = auth(world["users"]["owner"]["token"])
    log = client.get("/v1/integrations/deliveries?status=failed", headers=owner).json()
    assert [x["id"] for x in log] == [did]
    sink.fail_next = 0
    assert client.post(f"/v1/integrations/deliveries/{did}/redeliver", headers=owner).json()["status"] == "delivered"


def test_integrations_console_app_management(client):
    world = make_tenant()
    owner = auth(world["users"]["owner"]["token"])
    created = client.post("/v1/integrations/apps", json={"name": "Drafting bot", "webhook_url": "https://x.test/h"}, headers=owner).json()
    assert created["api_key"].startswith("por_") and created["webhook_secret"]
    assert "api_key" not in client.get("/v1/integrations/apps", headers=owner).json()[0]
    rotated = client.post(f"/v1/integrations/apps/{created['id']}/rotate-key", headers=owner).json()
    assert client.get("/v1/workflows", headers={"X-API-Key": created["api_key"]}).status_code == 401
    assert client.get("/v1/workflows", headers={"X-API-Key": rotated["api_key"]}).status_code == 200
    client.patch(f"/v1/integrations/apps/{created['id']}", json={"active": False}, headers=owner)
    assert client.get("/v1/workflows", headers={"X-API-Key": rotated["api_key"]}).status_code == 401
    conn = client.post("/v1/integrations/connectors", json={"source_app_id": world["app_id"], "kind": "jira",
                                                            "config": {"api_token": "secret", "base_url": "https://j"}}, headers=owner).json()
    assert conn["config"]["api_token"] == "***"


# --- MCP --------------------------------------------------------------------------------


@pytest.fixture()
def mcp(client):
    from api.main import app
    from mcp_server import tools

    def bind(world):
        tools.set_client_factory(lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"X-API-Key": world["api_key"]}))

    yield bind
    tools.set_client_factory(tools.default_client)


async def test_mcp_submit_wait_and_feedback(client, mcp, sink, tmp_path):
    from mcp_server import tools

    world = make_tenant()
    mcp(world)
    path = tmp_path / "draft.txt"
    path.write_text("Proposed reply: we will refund £120 within 14 days.\n\nPlease confirm before sending.")
    sub = await tools.submit_for_review(world["workflow_id"], file_path=str(path), external_ref="agent-1", ai_model="claude")
    assert sub["status"] == "in_review" and not sub["is_final"]

    pending = await tools.wait_for_decision(sub["submission_id"], timeout_seconds=0.2, poll_interval_seconds=0.1)
    assert pending["timed_out"] is True

    who, item = task_for(client, world, sub["submission_id"])
    token = world["users"][who]["token"]
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(token)).json()
    span = view["document"]["sections"][0]["spans"][0]["id"]
    client.post(f"/v1/review/tasks/{item['task_id']}/comments", json={"span_id": span, "body": "Refund window is 30 days"}, headers=auth(token))
    review(client, world, sub["submission_id"], "request_changes", reason="Wrong refund window")

    decided = await tools.wait_for_decision(sub["submission_id"], timeout_seconds=1)
    assert decided["status"] == "changes_requested" and decided["decision"]["reason"] == "Wrong refund window"
    assert "reviewer_ids" not in json.dumps(decided) and "assurance" not in json.dumps(decided)
    fb = await tools.get_review_feedback(sub["submission_id"])
    assert fb["comments"][0]["comment"] == "Refund window is 30 days" and fb["next_step"]

    again = await tools.submit_for_review(world["workflow_id"], content="Revised: refund within 30 days.",
                                          previous_submission_id=sub["submission_id"])
    assert again["status"] == "in_review"
    with pytest.raises(tools.ToolFailure):
        await tools.submit_for_review(world["workflow_id"])


async def test_mcp_server_exposes_the_four_tools(client, mcp):
    from mcp_server.server import build

    server = build("submitter")
    names = {t.name for t in await server.list_tools()}
    assert names == {"submit_for_review", "get_review_status", "wait_for_decision", "get_review_feedback"}
    world = make_tenant()
    mcp(world)
    result = await server.call_tool("submit_for_review", {"workflow_id": world["workflow_id"],
                                                          "file_base64": base64.b64encode(b"Hello reviewer, please check.").decode(),
                                                          "filename": "note.txt"})
    payload = result.structured_content or json.loads(result.content[0].text)
    data = payload.get("result", payload)
    status = await server.call_tool("get_review_status", {"submission_id": data["submission_id"]})
    sp = status.structured_content or json.loads(status.content[0].text)
    assert sp.get("result", sp)["status"] == "in_review"


# --- Connectors -------------------------------------------------------------------------


class FakeServices:
    """Records calls to Graph, Teams and Jira and returns canned responses."""

    def __init__(self, files):
        self.calls = []
        self.files = files

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, str(request.url), request.content))
        url = str(request.url)
        if "login.microsoftonline.com" in url:
            return httpx.Response(200, json={"access_token": "graph-token"})
        if url.endswith(":/children"):
            return httpx.Response(200, json={"value": [{"id": k, "name": v[0], "eTag": "1", "file": {}} for k, v in self.files.items()]})
        if "/content" in url:
            item = url.split("/items/")[1].split("/")[0]
            return httpx.Response(200, content=self.files[item][1])
        if "/rest/api/3/issue/" in url and request.method == "GET":
            return httpx.Response(200, json={"id": "1001", "fields": {"summary": "Draft letter", "description": {
                "type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Dear client, your premium is £420."}]}]}}})
        return httpx.Response(200, json={})


@pytest.fixture()
def external(client):
    from api.main import app
    from connectors import base

    def factory(app_id, tenant_id):
        from fastapi.testclient import TestClient

        return TestClient(app)

    base.set_por_client_factory(factory)

    def install(files=None):
        fake = FakeServices(files or {})
        base.set_transport(httpx.MockTransport(fake))
        return fake

    yield install
    base.set_transport(None)
    base.set_por_client_factory(None)


def add_connector(world, kind, config):
    from db.models import ConnectorConfig
    from db.session import session_scope

    with session_scope() as db:
        c = ConnectorConfig(tenant_id=world["tenant_id"], source_app_id=world["app_id"], kind=kind, config=config)
        db.add(c)
        db.flush()
        return c.id


def test_sharepoint_monitored_folder(client, external, sink):
    from scripts import sample_files

    fake = external({"item-1": ("letter.docx", sample_files.docx_bytes()), "item-2": ("ignore.exe", b"MZ")})
    world = make_tenant()
    cid = add_connector(world, "sharepoint", {"aad_tenant_id": "aad", "client_id": "c", "client_secret": "s", "drive_id": "d1",
                                              "workflow_id": world["workflow_id"]})
    from connectors import sharepoint
    from db.models import ConnectorConfig
    from db.session import session_scope

    with session_scope() as db:
        out = sharepoint.run_once(db, db.get(ConnectorConfig, cid))
    assert len(out["submitted"]) == 1 and out["completed"] == []
    sid = out["submitted"][0]
    with session_scope() as db:
        assert sharepoint.run_once(db, db.get(ConnectorConfig, cid))["submitted"] == []  # not resubmitted

    review(client, world, sid, "approve")
    with session_scope() as db:
        out = sharepoint.run_once(db, db.get(ConnectorConfig, cid))
    assert out["completed"] == [sid]
    patches = [(u, json.loads(c)) for m, u, c in fake.calls if m == "PATCH"]
    assert patches[0][1]["ReviewStatus"] == "approved" and patches[0][1]["ReviewAssuranceScore"] is not None
    assert patches[1][1]["parentReference"]["path"].endswith("Review/Approved")


def test_teams_nudges_reviewers_and_escalations(client, external, sink):
    fake = external()
    world = make_tenant(cap_policy={"daily_minutes": 3, "overflow": "escalate"}, reviewers=1)
    add_connector(world, "teams", {"webhook_url": "https://teams.example/webhook", "app_base_url": "https://por.example"})
    submit_text(client, world, text="Short note for review today please.", metadata={"title": "Short note"})
    submit_text(client, world, text="Another short note to review.")
    cards = [json.loads(c) for m, u, c in fake.calls if "teams.example" in u]
    texts = [json.dumps(c) for c in cards]
    assert any("New item in your review queue" in t and "https://por.example/review/" in t for t in texts)
    assert any("Review escalated to oversight" in t and "awaiting_capacity" in t for t in texts)


def test_jira_ticket_updated_with_decision_and_evidence(client, external, sink):
    fake = external()
    world = make_tenant()
    cid = add_connector(world, "jira", {"base_url": "https://acme.atlassian.net", "email": "bot@acme.test", "api_token": "t",
                                        "workflow_id": world["workflow_id"], "transitions": {"approved": "31"}})
    from connectors.jira import JiraConnector
    from db.models import ConnectorConfig
    from db.session import session_scope

    with session_scope() as db:
        sid = JiraConnector(db.get(ConnectorConfig, cid)).submit_issue("FIN-42")
    sub = client.get(f"/v1/submissions/{sid}", headers={"X-API-Key": world["api_key"]}).json()
    assert sub["metadata"]["jira_issue_key"] == "FIN-42"
    review(client, world, sid, "approve")
    posts = [(u, c) for m, u, c in fake.calls if m == "POST"]
    urls = [u for u, _ in posts]
    assert any(u.endswith("/issue/FIN-42/comment") for u in urls)
    assert any(u.endswith("/issue/FIN-42/transitions") for u in urls)
    attachment = next(c for u, c in posts if u.endswith("/issue/FIN-42/attachments"))
    assert b"%PDF" in attachment
