"""Workflow and assignment fixes: SLA reassignment, team ceilings, admin reviewers, assurance policy, score
persistence, load windows, the telemetry "flag" action, and retrying deferred items."""

from datetime import timedelta

from sqlalchemy import func, select

from tests.conftest import auth, make_tenant, review, submit_text, task_for


def _db():
    from db.session import session_scope

    return session_scope()


def _overdue(task_id):
    from db.models import ReviewTask, utcnow

    with _db() as db:
        db.get(ReviewTask, task_id).due_at = utcnow() - timedelta(minutes=5)


def _set_stage_policy(world, **stage):
    from db.models import Workflow

    with _db() as db:
        wf = db.scalars(select(Workflow).where(Workflow.family_id == world["workflow_id"])).one()
        wf.stages = [{"name": "Review", "team_id": world["team_id"], "sla_hours": 1, **stage}]


def test_sla_reassign_keeps_the_task_when_nobody_else_can_take_it(client, sink):
    from db.models import AuditEvent, ReviewTask
    from services import workflow

    world = make_tenant(reviewers=1)
    _set_stage_policy(world, on_sla_breach="reassign")
    sub = submit_text(client, world)
    who, item = task_for(client, world, sub["id"])
    _overdue(item["task_id"])
    with _db() as db:
        assert workflow.check_slas(db) == 1
    with _db() as db:
        assert db.get(ReviewTask, item["task_id"]).status == "assigned"
        assert db.scalars(select(AuditEvent).where(AuditEvent.action == "task.reassign_failed")).first() is not None
    assert task_for(client, world, sub["id"])[0] == who
    assert any(e["event"] == "submission.escalated" for e in sink.events())


def test_sla_reassignment_is_not_handed_back_to_the_slow_reviewer(client, sink):
    from services import assignment, workflow

    world = make_tenant(reviewers=2)
    _set_stage_policy(world, on_sla_breach="reassign")
    sub = submit_text(client, world)
    first, item = task_for(client, world, sub["id"])
    _overdue(item["task_id"])
    with _db() as db:
        workflow.check_slas(db)
    second, item2 = task_for(client, world, sub["id"])
    assert second != first
    with _db() as db:
        assert world["users"][first]["id"] in assignment.prior_reviewer_ids(db, sub["id"])
    # The new reviewer is deactivated: the item must not go back to the slow reviewer, so it waits for capacity.
    with _db() as db:
        from db.models import Submission, User

        db.get(User, world["users"][second]["id"]).active = False
        db.flush()
        workflow.reassign_open_tasks(db, world["users"][second]["id"], "user:test", "deactivated")

        assert db.get(Submission, sub["id"]).hold_reason == "awaiting_capacity"


def test_team_ceiling_counts_every_member_and_skips_reviewers_without_a_team(client, sink):
    from db.models import ReviewTask, User, Workflow

    world = make_tenant(reviewers=2)
    first = submit_text(client, world)
    holder, item = task_for(client, world, first["id"])
    with _db() as db:
        effort = db.get(ReviewTask, item["task_id"]).effort_minutes
        wf = db.scalars(select(Workflow).where(Workflow.family_id == world["workflow_id"])).one()
        wf.cap_policy = {"team_daily_minutes": effort * 1.5}
    # Written by the reviewer holding the first task, so they are not a candidate; their load still counts.
    author = f"{holder}@acme.test"
    second = submit_text(client, world, external_ref="T2", metadata={"author_email": author})
    from db.models import Submission

    with _db() as db:
        assert db.get(Submission, second["id"]).hold_reason == "awaiting_capacity"
    # A workflow-level team ceiling does not make a tenant-wide pseudo-team of reviewers in no team.
    with _db() as db:
        for u in db.scalars(select(User).where(User.tenant_id == world["tenant_id"])).all():
            u.team_ids = []
        wf = db.scalars(select(Workflow).where(Workflow.family_id == world["workflow_id"])).one()
        wf.stages = [{"name": "Review", "team_id": None, "sla_hours": 24}]
    third = submit_text(client, world, external_ref="T3")
    assert task_for(client, world, third["id"])


def test_admins_do_not_review_unless_the_tenant_allows_it(client):
    from db.models import Submission, Tenant, User, Workflow
    from services import assignment

    world = make_tenant(reviewers=1)
    sub = submit_text(client, world)
    with _db() as db:
        admin = db.get(User, world["users"]["admin"]["id"])
        admin.roles = ["admin", "reviewer"]
        s = db.get(Submission, sub["id"])
        wf = db.get(Workflow, s.workflow_id)
        ids = {u.id for u in assignment.eligible_reviewers(db, s, wf, {"team_id": None}, set())}
        assert admin.id not in ids
        t = db.get(Tenant, world["tenant_id"])
        t.settings = {**(t.settings or {}), "allow_admin_reviewers": True}
        db.flush()
        ids = {u.id for u in assignment.eligible_reviewers(db, s, wf, {"team_id": None}, set())}
        assert admin.id in ids


def test_assurance_policy_is_validated_and_weights_normalised(client):
    from db.models import Workflow
    from services import assurance

    world = make_tenant()
    lead = auth(world["users"]["lead"]["token"])
    url = f"/v1/admin/workflows/{world['workflow_id']}"
    base = {"name": "Default", "stages": [{"name": "Review"}]}
    for bad in ({"weights": {"bogus": 0.5}}, {"weights": {"load": 2}}, {"weights": {"load": "x"}},
                {"min_score": 150}, {"min_score": "high"}, {"weights": {"load": 0}}):
        r = client.put(url, headers=lead, json={**base, "assurance_policy": bad})
        assert r.status_code == 422, (bad, r.text)
    r = client.put(url, headers=lead, json={**base, "assurance_policy": {"weights": {"load": 1, "reading_coverage": 1},
                                                                       "min_score": 50}})
    assert r.status_code == 200, r.text
    # Bad stored values (from before validation) are ignored or coerced, never crash the score.
    w = Workflow(assurance_policy={"weights": {"load": "0.5", "bogus": 3, "reading_coverage": None}, "min_score": "x",
                                   "below_action": "explode"})
    weights = assurance.weights_for(w)
    assert abs(sum(weights.values()) - 1) < 1e-9 and "bogus" not in weights
    policy = assurance.policy_for(w)
    assert policy["min_score"] == 60 and policy["below_action"] == "release_flag"


def test_score_is_persisted_once_per_decision(client, sink):
    from db.models import AssuranceScore, AuditEvent

    world = make_tenant()
    sub = submit_text(client, world)
    review(client, world, sub["id"], "approve")
    with _db() as db:
        assert db.scalar(select(func.count()).select_from(AssuranceScore)
                         .where(AssuranceScore.submission_id == sub["id"])) == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.entity_id == sub["id"], AuditEvent.action == "assurance.scored")) == 1


def test_load_position_counts_only_that_day(client):
    from db.models import Workflow, utcnow
    from services import assignment

    world = make_tenant()
    sub = submit_text(client, world)
    who, _ = task_for(client, world, sub["id"])
    with _db() as db:
        wf = db.scalars(select(Workflow)).first()
        uid = world["users"][who]["id"]
        assert assignment.load_position(db, uid, wf) > 0
        assert assignment.load_position(db, uid, wf, utcnow() - timedelta(days=2)) == 0


def test_flag_action_marks_the_decision_and_forces_a_spot_check(client, sink):
    world = make_tenant(spot_check_rate=0.001, telemetry_policy={"min_reading_ratio": 0.3, "min_coverage": 0.6,
                                                                 "action": "flag"})
    sub = submit_text(client, world)
    _, result = review(client, world, sub["id"], "approve", read=False)
    assert result["decision"]["warnings"] and result["telemetry"]["review_standard"] == "flagged"
    lead = auth(world["users"]["lead"]["token"])
    assert client.get(f"/v1/oversight/submissions/{sub['id']}", headers=lead).json()["status"] == "spot_check"
    rows = client.get("/v1/reports/reviewers", headers=lead).json()["rows"]
    assert sum(r["warnings_flagged"] for r in rows) == 1 and sum(r["warnings_confirmed"] for r in rows) == 0


def test_warn_action_counts_confirmed_warnings(client, sink):
    world = make_tenant()
    sub = submit_text(client, world)
    review(client, world, sub["id"], "approve", read=False)
    rows = client.get("/v1/reports/reviewers", headers=auth(world["users"]["lead"]["token"])).json()["rows"]
    assert sum(r["warnings_confirmed"] for r in rows) == 1 and sum(r["warnings_flagged"] for r in rows) == 0


def _deferred_world(client):
    world = make_tenant(cap_policy={"daily_minutes": 4, "hourly_minutes": 4, "overflow": "defer"}, reviewers=1)
    submit_text(client, world, external_ref="F1")
    second = submit_text(client, world, external_ref="F2")
    return world, second


def test_purged_items_cannot_be_retried(client, sink):
    from db.models import Submission
    from services import retention

    world, second = _deferred_world(client)
    lead = auth(world["users"]["lead"]["token"])
    with _db() as db:
        retention.purge_submission(db, db.get(Submission, second["id"]), "user:test", "request")
    row = client.get(f"/v1/oversight/submissions/{second['id']}", headers=lead).json()
    assert row["status"] == "deferred" and "retry" not in row["actions"]
    r = client.post(f"/v1/oversight/submissions/{second['id']}/action", headers=lead, json={"action": "retry"})
    assert r.status_code == 409 and "purged" in r.text


def test_release_check_failure_cannot_be_retried_into_review(client, sink):
    from db.models import Document, Span

    world = make_tenant()
    sub = submit_text(client, world)
    with _db() as db:
        doc = db.scalars(select(Document).where(Document.submission_id == sub["id"])).one()
        db.scalars(select(Span).where(Span.document_id == doc.id)).first().text += " (altered)"
    review(client, world, sub["id"], "approve")
    lead = auth(world["users"]["lead"]["token"])
    url = f"/v1/oversight/submissions/{sub['id']}/action"
    assert client.post(url, headers=lead, json={"action": "defer", "reason": "tampered"}).status_code == 200
    row = client.get(f"/v1/oversight/submissions/{sub['id']}", headers=lead).json()
    assert "retry" not in row["actions"]
    r = client.post(url, headers=lead, json={"action": "retry"})
    assert r.status_code == 409 and "release check" in r.text


def test_deferred_spot_check_resumes_the_spot_check(client, sink):
    world = make_tenant(spot_check_rate=1.0, reviewers=1)
    sub = submit_text(client, world)
    review(client, world, sub["id"], "approve")
    lead = auth(world["users"]["lead"]["token"])
    url = f"/v1/oversight/submissions/{sub['id']}/action"
    row = client.get(f"/v1/oversight/submissions/{sub['id']}", headers=lead).json()
    assert row["hold_reason"] == "awaiting_spot_check_reviewer"
    assert client.post(url, headers=lead, json={"action": "defer", "reason": "no checker"}).status_code == 200
    admin = auth(world["users"]["admin"]["token"])
    client.post("/v1/admin/users", headers=admin, json={"email": "extra@acme.test", "roles": ["reviewer"],
                                                        "team_ids": [world["team_id"]]})
    r = client.post(url, headers=lead, json={"action": "retry"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "spot_check"
    detail = client.get(f"/v1/oversight/submissions/{sub['id']}", headers=lead).json()
    assert [t["kind"] for t in detail["reviews"]].count("primary") == 1
    assert any(t["kind"] == "spot_check" and t["status"] == "assigned" for t in detail["reviews"])
