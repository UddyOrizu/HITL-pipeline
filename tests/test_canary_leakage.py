"""Engineering rule: canary text must never appear in any outbound payload.

Every webhook body and every source-app API response (and MCP tool output) is collected across scenarios
designed to tempt a leak: reviewers quoting the canary in comments, edits, reasons and spot-checks.
"""

import json
import random

import httpx
import pymupdf
import pytest
from sqlalchemy import select

from tests.conftest import ack_all_flags, auth, make_tenant, read_fully, task_for


def canary_for(sid):
    from db.models import Canary
    from db.session import session_scope

    with session_scope() as db:
        c = db.scalars(select(Canary).where(Canary.submission_id == sid)).one()
        return c.span_id, c.injected_text


def decide(client, world, sid, outcome, *, quote_canary=False, cite_canary=False, comment_on_canary=False, kind=None, genuine=True):
    who, item = task_for(client, world, sid, kind)
    token = world["users"][who]["token"]
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(token)).json()
    span_ids = {sp["id"] for s in view["document"]["sections"] for sp in s["spans"]}
    cspan, ctext = canary_for(sid)
    genuine_span = next(sp["id"] for s in view["document"]["sections"] for sp in s["spans"] if sp["id"] != cspan)
    if comment_on_canary and cspan in span_ids:
        client.post(f"/v1/review/tasks/{item['task_id']}/comments",
                    json={"span_id": cspan, "kind": "edit", "body": f"Wrong: '{ctext}'", "suggested_text": "Remove this sentence."},
                    headers=auth(token))
    if quote_canary:
        client.post(f"/v1/review/tasks/{item['task_id']}/comments",
                    json={"span_id": genuine_span, "body": f"Compare with: {ctext}"}, headers=auth(token))
    read_fully(client, token, item["task_id"], view)
    ack_all_flags(client, token, item["task_id"], view)
    cited = ([cspan] if cite_canary and cspan in span_ids else []) + ([genuine_span] if genuine else [])
    reason = f"Problem with the statement '{ctext}' and the figures" if quote_canary else "See comments"
    r = client.post(f"/v1/review/tasks/{item['task_id']}/decision",
                    json={"outcome": outcome, "reason": reason, "cited_span_ids": cited, "confirm_warnings": True}, headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def scenario(client, sink):
    from services import canary

    canary.set_rng(random.Random(99))
    world = make_tenant(canary_rate=1.0, spot_check_rate=1.0)
    h = {"X-API-Key": world["api_key"]}
    responses = []

    def call(method, path, **kw):
        r = client.request(method, path, headers=h, **kw)
        responses.append(r)
        return r

    from scripts.sample_files import plain_text

    subs = []
    for i, (outcome, opts) in enumerate([
        ("reject", {"quote_canary": True, "cite_canary": True, "comment_on_canary": True}),
        ("request_changes", {"quote_canary": True, "comment_on_canary": True}),
        ("approve", {"comment_on_canary": True}),
        ("approve", {}),
    ]):
        sid = call("POST", "/v1/submissions", json={"workflow_id": world["workflow_id"], "text": plain_text(),
                                                    "external_ref": f"LEAK-{i}"}).json()["id"]
        subs.append(sid)
        decide(client, world, sid, outcome, **opts)
        call("GET", f"/v1/submissions/{sid}")
        decide(client, world, sid, outcome if i != 0 else "approve", kind="spot_check", quote_canary=True)
    # A disagreement resolved by oversight with a reason quoting the canary.
    lead = auth(world["users"]["lead"]["token"])
    for sid in subs:
        s = call("GET", f"/v1/submissions/{sid}").json()
        if s["status"] == "spot_check":
            _, ctext = canary_for(sid)
            client.post(f"/v1/oversight/submissions/{sid}/resolve", json={"outcome": "reject", "reason": f"Because of: {ctext}"}, headers=lead)

    for sid in subs:
        call("GET", f"/v1/submissions/{sid}")
        call("GET", f"/v1/submissions/{sid}/feedback")
        call("GET", f"/v1/submissions/{sid}/evidence")
        call("GET", f"/v1/submissions/{sid}/evidence", params={"format": "pdf"})
    call("GET", "/v1/workflows")
    call("GET", "/v1/assurance/summary")
    return world, subs, responses, sink


def test_no_canary_text_in_any_webhook_or_api_response(scenario):
    world, subs, responses, sink = scenario
    canaries = [canary_for(s)[1] for s in subs]
    assert all(canaries)

    bodies = []
    for r in responses:
        assert r.status_code < 500, r.text
        if r.headers.get("content-type", "").startswith("application/pdf"):
            bodies.append("".join(p.get_text() for p in pymupdf.open(stream=r.content, filetype="pdf")))
        else:
            bodies.append(r.text)
    bodies += [req.content.decode() for req in sink.requests]
    decided = [e for e in sink.events() if e["event"] == "submission.decided"]
    assert len(decided) == len(subs)
    assert any(e["event"] == "submission.escalated" for e in sink.events())

    def norm(s):
        return " ".join(s.split()).lower()

    for text in canaries:
        needle = norm(text)
        for body in bodies:
            hay = norm(json.loads(body) if body.startswith('"') else body)
            assert needle not in hay, "canary text leaked into an outbound payload"
            assert norm(json.dumps(text)[1:-1]) not in hay


async def test_no_canary_text_in_mcp_tool_output(scenario, client):
    from api.main import app
    from mcp_server import tools

    world, subs, _, _ = scenario
    tools.set_client_factory(lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                                       headers={"X-API-Key": world["api_key"]}))
    try:
        for sid in subs:
            out = json.dumps([await tools.get_review_status(sid), await tools.get_review_feedback(sid),
                              await tools.wait_for_decision(sid, timeout_seconds=0)])
            assert canary_for(sid)[1].lower() not in out.lower()
    finally:
        tools.set_client_factory(tools.default_client)


def test_guard_blocks_a_leaking_payload(client):
    world = make_tenant(canary_rate=1.0)
    sid = client.post("/v1/submissions", json={"workflow_id": world["workflow_id"], "text": "Para one is here.\n\nPara two has 12 items.\n\nPara three ends it."},
                      headers={"X-API-Key": world["api_key"]}).json()["id"]
    _, ctext = canary_for(sid)
    from db.session import session_scope
    from services.outbound import CanaryLeakError, assert_no_canary_leak, redact

    with session_scope() as db:
        with pytest.raises(CanaryLeakError):
            assert_no_canary_leak(db, {"nested": [{"text": f"prefix {ctext.upper()} suffix"}]}, [sid])
        assert_no_canary_leak(db, {"text": "harmless"}, [sid])
        from services.outbound import canaries_for

        assert redact(f"quote: {ctext}", canaries_for(db, [sid])).startswith("[redacted")
