"""Work can be submitted through the API, the web portal and MCP, and reviewed through the web app, the API and MCP.

Every combination goes through the same pipeline and controls; the channel is recorded on each decision.
"""

import base64
import json

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import ack_all_flags, auth, make_tenant, read_fully, submit_text, task_for

TEXT = ("Revenue rose to £4.2m in 2025.\n\nWe recommend the client relies on this to meet its FCA obligations.\n\n"
        "The board will review the plan next quarter.\n\nThank you for reading this note.")


def ui(token):
    return {**auth(token), "X-PoR-Channel": "ui"}


def canary_text(sid):
    from db.models import Canary
    from db.session import session_scope

    with session_scope() as db:
        c = db.scalars(select(Canary).where(Canary.submission_id == sid)).first()
        return c.injected_text if c else None


@pytest.fixture()
def mcp_clients():
    """Point both MCP modes at the in-process app."""
    from api.main import app
    from mcp_server import tools

    def bind(app_key=None, reviewer_token=None):
        def make(headers):
            return lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                             headers={**headers, **tools.CHANNEL})
        if app_key:
            tools.set_client_factory(make({"X-API-Key": app_key}))
        if reviewer_token:
            tools.set_reviewer_client_factory(make({"Authorization": f"Bearer {reviewer_token}"}))

    yield bind
    tools.set_client_factory(tools.default_client)
    tools.set_reviewer_client_factory(tools.default_reviewer_client)


# --- Submitting -------------------------------------------------------------------------


async def test_submit_through_api_portal_and_mcp(client, mcp_clients):
    from mcp_server import tools

    world = make_tenant()
    via_api = submit_text(client, world, text=TEXT, external_ref="api-1")
    sub_token = world["users"]["sub"]["token"]
    via_portal = client.post("/v1/portal/submissions", data={"workflow_id": world["workflow_id"], "risk_tier": "high"},
                             files={"file": ("letter.txt", TEXT.encode(), "text/plain")}, headers=ui(sub_token))
    assert via_portal.status_code == 201, via_portal.text
    via_portal = via_portal.json()
    mcp_clients(app_key=world["api_key"])
    via_mcp = await tools.submit_for_review(world["workflow_id"], content=TEXT, external_ref="mcp-1")

    for sid in (via_api["id"], via_portal["id"], via_mcp["submission_id"]):
        who, _ = task_for(client, world, sid)
        assert who.startswith("r")  # every channel lands in the same review queue
    assert via_portal["status"] == "in_review" and via_portal["risk_tier"] == "high" and via_portal["title"] == "letter.txt"

    from db.models import AuditEvent
    from db.session import session_scope

    with session_scope() as db:
        channels = {e.entity_id: e.payload["channel"] for e in db.scalars(select(AuditEvent).where(
            AuditEvent.tenant_id == world["tenant_id"], AuditEvent.action == "submission.received")).all()}
    assert channels == {via_api["id"]: "api", via_portal["id"]: "ui", via_mcp["submission_id"]: "mcp"}

    # Submitters see only their own portal items; source apps keep their own list.
    mine = client.get("/v1/portal/submissions", headers=ui(sub_token)).json()
    assert [s["id"] for s in mine] == [via_portal["id"]]
    assert client.get(f"/v1/portal/submissions/{via_api['id']}", headers=ui(sub_token)).status_code == 404
    other = client.post("/v1/admin/users", json={"email": "sub2@acme.test", "roles": ["submitter"]},
                        headers=auth(world["users"]["admin"]["token"])).json()
    from services.security import issue_token

    other_token = issue_token(other["id"], world["tenant_id"], "user", ["submitter"])
    assert client.get(f"/v1/portal/submissions/{via_portal['id']}", headers=auth(other_token)).status_code == 404
    assert client.get("/v1/portal/submissions", headers=auth(world["users"]["r1"]["token"])).status_code == 403


def test_portal_submitters_never_review_their_own_work(client):
    world = make_tenant(reviewers=2)
    admin = auth(world["users"]["admin"]["token"])
    both = client.post("/v1/admin/users", json={"email": "both@acme.test", "roles": ["reviewer", "submitter"],
                                               "team_ids": [world["team_id"]]}, headers=admin).json()
    from services.security import issue_token

    token = issue_token(both["id"], world["tenant_id"], "user", ["reviewer", "submitter"])
    for _ in range(4):
        sid = client.post("/v1/portal/submissions", json={"workflow_id": world["workflow_id"], "text": TEXT,
                                                          "metadata": {"author_email": "someone-else@acme.test"}},
                          headers=ui(token)).json()["id"]
        queue = client.get("/v1/review/queue", headers=auth(token)).json()
        assert sid not in [q["submission_id"] for q in queue]


def test_portal_view_never_shows_canaries(client):
    world = make_tenant(canary_rate=1.0)
    token = world["users"]["sub"]["token"]
    sid = client.post("/v1/portal/submissions", json={"workflow_id": world["workflow_id"], "text": TEXT}, headers=ui(token)).json()["id"]
    who, item = task_for(client, world, sid)
    rtoken = world["users"][who]["token"]
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=ui(rtoken)).json()
    span = next(sp["id"] for s in view["document"]["sections"] for sp in s["spans"] if sp["text"] == canary_text(sid))
    client.post(f"/v1/review/tasks/{item['task_id']}/comments", json={"span_id": span, "body": f"Wrong: {canary_text(sid)}"}, headers=ui(rtoken))
    read_fully(client, rtoken, item["task_id"], view)
    ack_all_flags(client, rtoken, item["task_id"], view)
    client.post(f"/v1/review/tasks/{item['task_id']}/decision",
                json={"outcome": "request_changes", "reason": f"Please fix: {canary_text(sid)}", "cited_span_ids": [span, view["document"]["sections"][0]["spans"][0]["id"]]},
                headers=ui(rtoken))
    outputs = json.dumps([client.get(f"/v1/portal/submissions/{sid}", headers=ui(token)).json(),
                          client.get("/v1/portal/submissions", headers=ui(token)).json()])
    assert canary_text(sid).lower() not in outputs.lower()


# --- Reviewing --------------------------------------------------------------------------


def test_review_and_approve_through_the_web_app(client, sink):
    world = make_tenant()
    sid = submit_text(client, world, text=TEXT)["id"]
    who, item = task_for(client, world, sid)
    token = world["users"][who]["token"]
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=ui(token)).json()
    read_fully(client, token, item["task_id"], view)  # the workspace sends reading telemetry
    ack_all_flags(client, token, item["task_id"], view)
    r = client.post(f"/v1/review/tasks/{item['task_id']}/decision", json={"outcome": "approve"}, headers=ui(token)).json()
    assert r["status"] == "recorded" and r["decision"]["channel"] == "ui" and r["decision"]["warnings"] == []
    assert client.get(f"/v1/submissions/{sid}", headers={"X-API-Key": world["api_key"]}).json()["status"] == "approved"


def test_review_and_approve_through_the_api(client, sink):
    world = make_tenant()
    sid = submit_text(client, world, text=TEXT)["id"]
    who, item = task_for(client, world, sid)
    h = auth(world["users"][who]["token"])  # no channel header: a direct API client
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=h).json()
    url = f"/v1/review/tasks/{item['task_id']}/decision"
    blocked = client.post(url, json={"outcome": "approve"}, headers=h)
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "mandatory_flags"
    ack_all_flags(client, world["users"][who]["token"], item["task_id"], view)
    # No reading telemetry from an API client, so the workflow's reading standard asks for confirmation.
    warn = client.post(url, json={"outcome": "approve"}, headers=h)
    assert warn.status_code == 409 and warn.json()["detail"]["code"] == "confirm_required"
    r = client.post(url, json={"outcome": "approve", "confirm_warnings": True}, headers=h).json()
    assert r["status"] == "recorded" and r["decision"]["channel"] == "api" and r["decision"]["warnings"]
    assert client.get(f"/v1/submissions/{sid}", headers={"X-API-Key": world["api_key"]}).json()["status"] == "approved"


async def test_review_and_approve_through_mcp(client, sink, mcp_clients):
    from mcp_server import tools

    world = make_tenant()
    sid = submit_text(client, world, text=TEXT)["id"]
    who, item = task_for(client, world, sid)
    mcp_clients(reviewer_token=world["users"][who]["token"])

    tasks = await tools.list_review_tasks()
    assert [t["submission_id"] for t in tasks] == [sid]
    opened = await tools.open_review_task(tasks[0]["task_id"])
    assert "Revenue rose to £4.2m" in opened["document_text"] and "[FLAG HIGH" in opened["document_text"]
    with pytest.raises(tools.ToolFailure, match="mandatory_flags"):
        await tools.submit_review_decision(item["task_id"], "approve")
    for flag_id in opened["must_acknowledge_before_approving"]:
        await tools.acknowledge_flag(item["task_id"], flag_id)
    first_span = opened["document_text"].split("[", 2)[1].split("]")[0]
    await tools.add_review_comment(item["task_id"], "Checked against the accounts.", span_id=first_span)
    with pytest.raises(tools.ToolFailure, match="confirm_required"):
        await tools.submit_review_decision(item["task_id"], "approve")
    r = await tools.submit_review_decision(item["task_id"], "approve", confirm_warnings=True)
    assert r["status"] == "recorded" and r["decision"]["channel"] == "mcp"
    assert await tools.list_review_tasks() == []

    record = client.get(f"/v1/submissions/{sid}/evidence", headers={"X-API-Key": world["api_key"]}).json()
    assert record["submission"]["status"] == "approved"
    assert [rv["decision"]["channel"] for rv in record["reviews"]] == ["mcp"]
    # MCP review cannot measure reading, so the assurance score reflects zero reading coverage.
    assert record["assurance"]["components"]["reading_coverage"]["value"] == 0


async def test_mcp_reject_and_request_changes_reach_the_submitting_agent(client, sink, mcp_clients):
    from mcp_server import tools

    world = make_tenant()
    mcp_clients(app_key=world["api_key"])
    sub = await tools.submit_for_review(world["workflow_id"], content=TEXT, external_ref="agent-1")
    who, item = task_for(client, world, sub["submission_id"])
    mcp_clients(reviewer_token=world["users"][who]["token"])
    opened = await tools.open_review_task(item["task_id"])
    span = opened["document_text"].split("[", 2)[1].split("]")[0]
    await tools.add_review_comment(item["task_id"], "Give the source for this figure.", span_id=span,
                                   suggested_text="Revenue rose to £4.2m in 2025 (audited accounts, note 3).")
    with pytest.raises(tools.ToolFailure, match="reason"):
        await tools.submit_review_decision(item["task_id"], "request_changes")
    r = await tools.submit_review_decision(item["task_id"], "request_changes", reason="Source the revenue figure.",
                                           cited_span_ids=[span])
    assert r["status"] == "recorded"
    fb = await tools.get_review_feedback(sub["submission_id"])
    assert fb["outcome"] == "changes_requested" and fb["comments"][0]["suggested_text"].endswith("note 3).")


def test_workflow_can_limit_which_channels_may_decide(client):
    world = make_tenant()
    lead = auth(world["users"]["lead"]["token"])
    wf = next(w for w in client.get("/v1/admin/workflows", headers=lead).json() if w["id"] == world["workflow_id"])
    body = {k: wf[k] for k in ("name", "stages", "canary_rate", "canary_domain", "cap_policy", "spot_check_rate",
                               "risk_policy", "assurance_policy", "source_app_ids")}
    body["telemetry_policy"] = {**wf["telemetry_policy"], "review_channels": ["ui"]}
    assert client.put(f"/v1/admin/workflows/{world['workflow_id']}", json=body, headers=lead).status_code == 200
    bad = {**body, "telemetry_policy": {**body["telemetry_policy"], "review_channels": ["fax"]}}
    assert client.put(f"/v1/admin/workflows/{world['workflow_id']}", json=bad, headers=lead).status_code == 422

    sid = submit_text(client, world, text=TEXT)["id"]
    who, item = task_for(client, world, sid)
    token = world["users"][who]["token"]
    url = f"/v1/review/tasks/{item['task_id']}/decision"
    for channel in ("api", "mcp"):
        r = client.post(url, json={"outcome": "reject", "reason": "no"}, headers={**auth(token), "X-PoR-Channel": channel})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "channel_not_allowed"
    r = client.post(url, json={"outcome": "reject", "reason": "Not ready."}, headers=ui(token))
    assert r.status_code == 200 and r.json()["decision"]["channel"] == "ui"


def test_one_mcp_server_cannot_both_submit_and_approve(monkeypatch):
    from mcp_server import server

    monkeypatch.setenv("POR_MCP_API_KEY", "por_x")
    monkeypatch.setenv("POR_MCP_REVIEWER_TOKEN", "t")
    with pytest.raises(SystemExit):
        server.mode()
    monkeypatch.delenv("POR_MCP_API_KEY")
    assert server.mode() == "reviewer"


def test_submitter_sees_decision_and_evidence_pack_in_portal(client, sink):
    world = make_tenant()
    token = world["users"]["sub"]["token"]
    sid = client.post("/v1/portal/submissions", json={"workflow_id": world["workflow_id"], "text": TEXT,
                                                      "file": None}, headers=ui(token)).json()["id"]
    from tests.conftest import review

    review(client, world, sid, "approve")
    s = client.get(f"/v1/portal/submissions/{sid}", headers=ui(token)).json()
    assert s["status"] == "approved" and s["decision"]["outcome"] == "approved" and s["has_evidence_pack"]
    pdf = client.get(f"/v1/portal/submissions/{sid}/evidence.pdf", headers=ui(token))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert base64.b64encode(pdf.content)  # downloadable
    # The portal app is platform-managed: not listed or editable in the integrations console.
    owner = auth(world["users"]["owner"]["token"])
    assert all(a["name"] != "Web portal" for a in client.get("/v1/integrations/apps", headers=owner).json())


async def test_mcp_clients_see_why_a_decision_was_refused(client, mcp_clients):
    """Expected failures must reach the MCP client with their reason, not just "Error executing tool"."""
    from mcp_server.server import build

    world = make_tenant()
    sid = submit_text(client, world, text=TEXT)["id"]
    who, item = task_for(client, world, sid)
    mcp_clients(reviewer_token=world["users"][who]["token"])
    server = build("reviewer")
    try:
        result = await server.call_tool("submit_review_decision", {"task_id": item["task_id"], "outcome": "approve"})
        text = json.dumps(result.model_dump() if hasattr(result, "model_dump") else str(result))
    except Exception as exc:  # noqa: BLE001 - some SDK versions raise instead of returning an error result
        text = str(exc)
    assert "mandatory_flags" in text and "acknowledged before approving" in text
