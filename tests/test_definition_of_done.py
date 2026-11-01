"""MVP definition of done, end to end on the seeded demo tenant.

A demo source app submits a .docx, .pptx and .pdf through the API and one through MCP; reviewers process them
with at least one canary caught and one missed; one item is spot-checked with disagreement; the source app
receives signed webhooks with decisions and assurance scores; an evidence pack downloads for each.
"""

import base64
import json

import httpx
from sqlalchemy import select

from scripts import sample_files
from tests.conftest import ack_all_flags, auth, read_fully


def login(client, email):
    return client.post("/v1/auth/dev-login", json={"email": email}).json()["access_token"]


def find_task(client, tokens, sid, kind=None):
    for email, token in tokens.items():
        for item in client.get("/v1/review/queue", headers=auth(token)).json():
            if item["submission_id"] == sid and (kind is None or item["kind"] == kind):
                return email, token, item
    raise AssertionError(f"no task for {sid}")


def canary_span(sid):
    from db.models import Canary
    from db.session import session_scope

    with session_scope() as db:
        return db.scalars(select(Canary.span_id).where(Canary.submission_id == sid)).first()


def work(client, tokens, sid, outcome="approve", catch_canary=False, kind=None, reason=None):
    email, token, item = find_task(client, tokens, sid, kind)
    tid = item["task_id"]
    view = client.get(f"/v1/review/tasks/{tid}", headers=auth(token)).json()
    cspan = canary_span(sid)
    if catch_canary:
        assert cspan in {sp["id"] for s in view["document"]["sections"] for sp in s["spans"]}
        client.post(f"/v1/review/tasks/{tid}/comments", json={"span_id": cspan, "body": "This is not right"}, headers=auth(token))
    read_fully(client, token, tid, view)
    ack_all_flags(client, token, tid, view)
    genuine = view["document"]["sections"][0]["spans"][-1]["id"]
    r = client.post(f"/v1/review/tasks/{tid}/decision", headers=auth(token), json={
        "outcome": outcome, "reason": reason or ("Checked" if outcome == "approve" else "Figures need correcting"),
        "cited_span_ids": [] if outcome == "approve" else [genuine], "confirm_warnings": True})
    assert r.status_code == 200, r.text
    return email, r.json()


async def test_definition_of_done(client, sink, tmp_path, monkeypatch):
    from scripts import seed as seed_module

    monkeypatch.setattr(seed_module, "OUT", tmp_path / "seed")
    info = seed_module.seed(create_tables=False)
    api = {"X-API-Key": info["api_key"]}
    owner = login(client, "ian@demo.test")
    lead = login(client, "olu@demo.test")
    admin = login(client, "admin@demo.test")
    client.patch(f"/v1/integrations/apps/{info['source_app_id']}", json={"webhook_url": "https://demo-app.test/hooks"}, headers=auth(owner))

    # Deterministic controls for the demo: every review seeded; spot-checks on the PDF workflow.
    teams = client.get("/v1/admin/teams", headers=auth(lead)).json()
    stage = [{"name": "Review", "team_id": teams[0]["id"], "sla_hours": 24}]
    seeded = client.post("/v1/admin/workflows", headers=auth(lead), json={
        "name": "DoD letters", "stages": stage, "canary_rate": 1.0, "spot_check_rate": 0.0, "canary_domain": "financial"}).json()
    checked = client.post("/v1/admin/workflows", headers=auth(lead), json={
        "name": "DoD reports", "stages": stage, "canary_rate": 1.0, "spot_check_rate": 1.0}).json()
    assert client.get("/v1/admin/users", headers=auth(admin)).status_code == 200

    def submit(name, data, wf):
        r = client.post("/v1/submissions", headers=api, files={"file": (name, data)},
                        data={"workflow_id": wf, "external_ref": name, "risk_tier": "medium", "ai_model": "demo-llm",
                              "metadata": json.dumps({"title": name})})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    docx = submit("mortgage-letter.docx", sample_files.docx_bytes(), seeded["id"])
    pptx = submit("claims-review.pptx", sample_files.pptx_bytes(), seeded["id"])
    pdf = submit("transfer-pricing-report.pdf", sample_files.pdf_bytes(), checked["id"])

    from api.main import app
    from mcp_server import tools

    tools.set_client_factory(lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=api))
    try:
        mcp_sub = await tools.submit_for_review(
            seeded["id"], file_base64=base64.b64encode(sample_files.txt_bytes()).decode(), filename="letter.txt",
            external_ref="agent-run-7", ai_model="agent")
    finally:
        tools.set_client_factory(tools.default_client)
    via_mcp = mcp_sub["submission_id"]

    reviewers = {e: login(client, e) for e in ("ada@demo.test", "ben@demo.test", "cara@demo.test")}
    _, caught = work(client, reviewers, docx, "approve", catch_canary=True)
    _, missed = work(client, reviewers, pptx, "approve")
    _, _ = work(client, reviewers, via_mcp, "approve", catch_canary=True)
    assert caught["canary"][0]["caught"] is True
    assert missed["canary"][0]["caught"] is False

    first, _ = work(client, reviewers, pdf, "approve")
    second, _ = work(client, reviewers, pdf, "reject", kind="spot_check", reason="Royalty figure is unsupported")
    assert first != second
    held = client.get("/v1/oversight/submissions?held=true", headers=auth(lead)).json()
    assert [h["hold_reason"] for h in held] == ["spot_check_disagreement"]
    client.post(f"/v1/oversight/submissions/{pdf}/resolve", json={"outcome": "request_changes", "reason": "Add benchmarking evidence"},
                headers=auth(lead))

    from services.webhooks import verify

    decided = {}
    for req in sink.requests:
        assert verify(info["webhook_secret"], req.headers["X-PoR-Signature"], req.content)
        body = json.loads(req.content)
        if body["event"] == "submission.decided":
            decided[body["data"]["submission"]["id"]] = body["data"]["submission"]
    assert set(decided) == {docx, pptx, pdf, via_mcp}
    for sid, sub in decided.items():
        assert sub["decision"]["assurance"]["score"] is not None
        assert sub["decision"]["evidence_url"].endswith(f"/v1/submissions/{sid}/evidence")
    assert decided[pdf]["status"] == "changes_requested"
    assert decided[pdf]["decision"]["assurance"]["components"]["independent_check"]["value"] == 0.0

    for sid in decided:
        pack = client.get(f"/v1/submissions/{sid}/evidence", params={"format": "pdf"}, headers=api)
        assert pack.status_code == 200 and pack.content.startswith(b"%PDF")

    dash = client.get("/v1/oversight/dashboard", headers=auth(lead)).json()
    assert dash["catch_rate"]["overall"] is not None and dash["spot_checks"]["disagreements"] == 1
