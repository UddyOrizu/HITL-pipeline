"""Milestone 3: workflow state machine, stages, SLAs, escalation, versioning and the request-changes loop."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.conftest import auth, make_tenant, review, submit_text, task_for


def two_stage(**kw):
    return make_tenant(stages=[{"name": "Preparer", "sla_hours": 24}, {"name": "Partner", "sla_hours": 48}], **kw)


def status(client, world, sid):
    return client.get(f"/v1/submissions/{sid}", headers={"X-API-Key": world["api_key"]}).json()


def test_two_stage_flow_uses_different_reviewers(client, sink):
    world = two_stage()
    sub = submit_text(client, world)
    assert status(client, world, sub["id"])["current_stage"] == "Preparer"
    first, _ = review(client, world, sub["id"], "approve")
    s = status(client, world, sub["id"])
    assert s["status"] == "in_review" and s["current_stage"] == "Partner"
    second, item = task_for(client, world, sub["id"])
    assert second != first and item["stage"] == "Partner"
    # The partner sees the preparer's decision; spot-checkers never would.
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(world["users"][second]["token"])).json()
    assert view["prior_stages"][0]["outcome"] == "approve"
    review(client, world, sub["id"], "approve")
    assert status(client, world, sub["id"])["status"] == "approved"


def test_reject_at_first_stage_is_final(client, sink):
    world = two_stage()
    sub = submit_text(client, world)
    review(client, world, sub["id"], "reject", reason="Figures are unsupported")
    s = status(client, world, sub["id"])
    assert s["status"] == "rejected"
    assert s["decision"]["reason"] == "Figures are unsupported"


def test_request_changes_loop_with_feedback_and_resubmission(client, sink):
    world = make_tenant()
    sub = submit_text(client, world)
    who, item = task_for(client, world, sub["id"])
    token = world["users"][who]["token"]
    view = client.get(f"/v1/review/tasks/{item['task_id']}", headers=auth(token)).json()
    span = view["document"]["sections"][0]["spans"][1]
    client.post(f"/v1/review/tasks/{item['task_id']}/comments",
                json={"span_id": span["id"], "kind": "edit", "body": "Use the verified figure",
                      "suggested_text": span["text"].replace("£245,000", "£240,000")}, headers=auth(token))
    review(client, world, sub["id"], "request_changes", reason="Loan amount does not match the application")
    s = status(client, world, sub["id"])
    assert s["status"] == "changes_requested"
    assert s["decision"]["comments"][0]["suggested_text"].count("£240,000") == 1

    fb = client.get(f"/v1/submissions/{sub['id']}/feedback", headers={"X-API-Key": world["api_key"]}).json()
    assert fb["outcome"] == "changes_requested" and fb["comments"][0]["body"] == "Use the verified figure"

    v2 = submit_text(client, world, text="Revised letter: loan of £240,000 over 25 years.", previous_submission_id=sub["id"])
    assert v2["previous_submission_id"] == sub["id"] and v2["status"] == "in_review"


def test_cancel(client, sink):
    world = make_tenant()
    sub = submit_text(client, world)
    h = {"X-API-Key": world["api_key"]}
    assert client.post(f"/v1/submissions/{sub['id']}/cancel", headers=h).json()["status"] == "cancelled"
    assert client.post(f"/v1/submissions/{sub['id']}/cancel", headers=h).status_code == 409
    for key, u in world["users"].items():
        if key.startswith("r"):
            assert client.get("/v1/review/queue", headers=auth(u["token"])).json() == []


def test_invalid_transitions_are_refused(client):
    world = make_tenant()
    sub = submit_text(client, world)
    from db.models import Submission
    from db.session import session_scope
    from services import workflow

    with session_scope() as db:
        s = db.get(Submission, sub["id"])
        with pytest.raises(workflow.TransitionError):
            workflow.transition(db, s, "received", "test")
        with pytest.raises(workflow.TransitionError):
            workflow.transition(db, s, "processing", "test")


def test_sla_breach_escalates(client, sink):
    world = make_tenant()
    sub = submit_text(client, world)
    from db.models import ReviewTask, utcnow
    from db.session import session_scope
    from services import workflow

    with session_scope() as db:
        task = db.scalars(select(ReviewTask).where(ReviewTask.submission_id == sub["id"])).one()
        task.due_at = utcnow() - timedelta(minutes=1)
    with session_scope() as db:
        assert workflow.check_slas(db) == 1
    with session_scope() as db:
        assert workflow.check_slas(db) == 0  # escalated once only
    esc = [e for e in sink.events() if e["event"] == "submission.escalated"]
    assert esc and esc[0]["data"]["reason"] == "sla_breached"
    assert status(client, world, sub["id"])["escalated"] is True


def test_workflow_versioning_pins_submissions(client):
    world = make_tenant()
    lead = auth(world["users"]["lead"]["token"])
    old = submit_text(client, world)
    r = client.put(f"/v1/admin/workflows/{world['workflow_id']}", headers=lead,
                   json={"name": "Default v2", "stages": [{"name": "Only stage", "sla_hours": 8}], "canary_rate": 0})
    assert r.status_code == 200 and r.json()["version"] == 2
    new = submit_text(client, world)
    assert old["workflow_version"] == 1 and new["workflow_version"] == 2
    history = client.get("/v1/admin/workflows?include_history=true", headers=lead).json()
    assert [w["version"] for w in history if w["id"] == world["workflow_id"]] == [1, 2]
    bad = client.post("/v1/admin/workflows", headers=lead, json={"name": "x", "cap_policy": {"overflow": "drop"}})
    assert bad.status_code == 422
