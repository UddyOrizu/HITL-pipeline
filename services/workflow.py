"""Workflow engine: the approval flow as a Postgres-backed state machine.

Stages, SLAs and escalation, spot-check holds, thresholds, and release with the content-hash check.
"""

from __future__ import annotations

import hashlib
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Canary, Decision, Document, ReviewTask, Submission, Workflow, utcnow
from services import assignment, assurance, audit, canary, ingestion, webhooks
from services.storage import get_storage

TRANSITIONS: dict[str, set[str]] = {
    "received": {"processing", "cancelled", "deferred"},
    "processing": {"in_review", "deferred", "cancelled"},
    "in_review": {"spot_check", "approved", "rejected", "changes_requested", "deferred", "cancelled"},
    "spot_check": {"approved", "rejected", "changes_requested", "cancelled"},
    "approved": set(),
    "rejected": set(),
    "changes_requested": set(),
    "deferred": set(),
    "cancelled": set(),
}
FINAL = {"approved", "rejected", "changes_requested", "deferred", "cancelled"}
OUTCOME_STATUS = {"approve": "approved", "reject": "rejected", "request_changes": "changes_requested"}
HOLD_REASONS = {
    "awaiting_capacity", "awaiting_spot_check_reviewer", "spot_check_disagreement", "below_threshold", "release_check_failed",
}

_rng = random.SystemRandom()


def set_rng(rng: random.Random) -> None:
    global _rng
    _rng = rng


class TransitionError(ValueError):
    pass


class ReleaseCheckFailed(RuntimeError):
    pass


def transition(session: Session, sub: Submission, to: str, actor: str, **payload) -> None:
    if to not in TRANSITIONS.get(sub.status, set()):
        raise TransitionError(f"cannot move submission from {sub.status} to {to}")
    frm = sub.status
    sub.status = to
    sub.updated_at = utcnow()
    audit.record(session, sub.tenant_id, actor, "submission.status_changed", "submission", sub.id,
                 {"from": frm, "to": to, **payload})


def workflow_of(session: Session, sub: Submission) -> Workflow:
    return session.get(Workflow, sub.workflow_id)


def stages_of(wf: Workflow) -> list[dict]:
    return wf.stages or [{"name": "Review", "team_id": None, "sla_hours": 24}]


def escalate(session: Session, sub: Submission, reason: str, actor: str = "system:workflow", **extra) -> None:
    sub.escalated = True
    audit.record(session, sub.tenant_id, actor, "submission.escalated", "submission", sub.id, {"reason": reason, **extra})
    webhooks.emit(session, sub, "submission.escalated", {"reason": reason, **extra})


def hold(session: Session, sub: Submission, reason: str, escalate_now: bool = False, actor: str = "system:workflow") -> None:
    assert reason in HOLD_REASONS, reason
    sub.hold_reason = reason
    audit.record(session, sub.tenant_id, actor, "submission.held", "submission", sub.id, {"reason": reason})
    if escalate_now:
        escalate(session, sub, reason, actor)


# --- Starting and assigning -------------------------------------------------------------


def start_review(session: Session, sub: Submission) -> None:
    transition(session, sub, "in_review", "system:workflow")
    sub.stage_index = 0
    webhooks.emit(session, sub, "submission.in_review")
    assign_stage(session, sub)


def assign_stage(session: Session, sub: Submission) -> ReviewTask | None:
    wf = workflow_of(session, sub)
    stages = stages_of(wf)
    stage = stages[sub.stage_index]
    effort = assignment.estimate_effort(assignment.word_count(session, sub), sub.risk_tier)
    exclude = assignment.prior_reviewer_ids(session, sub.id)  # rotation: a different reviewer for each stage
    reviewer = assignment.pick_reviewer(session, sub, wf, stage, effort, exclude)
    if reviewer is None:
        return _overflow(session, sub, wf)
    task = assignment.create_task(session, sub, reviewer, sub.stage_index, stage, "primary", effort)
    sub.hold_reason = None
    if sub.stage_index == 0:
        for c in session.scalars(select(Canary).where(Canary.submission_id == sub.id, Canary.scored_task_id.is_(None))).all():
            c.scored_task_id = task.id
            c.scored_reviewer_id = reviewer.id
    audit.record(session, sub.tenant_id, "system:assignment", "task.assigned", "review_task", task.id,
                 {"submission_id": sub.id, "reviewer_id": reviewer.id, "stage": stage.get("name"), "kind": "primary",
                  "effort_minutes": effort})
    from services import jobs

    jobs.publish("task.assigned", {"tenant_id": sub.tenant_id, "task_id": task.id, "reviewer_id": reviewer.id,
                                    "submission_id": sub.id, "stage": stage.get("name"), "kind": "primary"}, session=session)
    return task


def _overflow(session: Session, sub: Submission, wf: Workflow) -> None:
    policy = assignment.caps_for(wf)["overflow"]
    if policy == "defer":
        transition(session, sub, "deferred", "system:assignment", reason="capacity")
        sub.hold_reason = None
        webhooks.emit(session, sub, "submission.deferred", {"reason": "capacity"})
        return None
    if sub.hold_reason != "awaiting_capacity":
        hold(session, sub, "awaiting_capacity", escalate_now=policy == "escalate")
    return None


def _sample_for_spot_check(session: Session, sub: Submission, wf: Workflow, reviewer_id: str) -> bool:
    rate = wf.spot_check_rate or 0
    if rate <= 0:
        return False
    if sub.risk_tier == "high":
        rate *= 2
    rate_value, n = canary.catch_rate(session, sub.tenant_id, reviewer_id)
    if n >= 3 and rate_value < 0.5:
        rate *= 2
    return _rng.random() < min(rate, 1.0)


def assign_spot_check(session: Session, sub: Submission) -> ReviewTask | None:
    wf = workflow_of(session, sub)
    stage = {"name": "Spot-check", "team_id": (wf.risk_policy or {}).get("spot_check_team_id"),
             "sla_hours": stages_of(wf)[-1].get("sla_hours")}
    effort = assignment.estimate_effort(assignment.word_count(session, sub), sub.risk_tier)
    reviewer = assignment.pick_reviewer(session, sub, wf, stage, effort, assignment.prior_reviewer_ids(session, sub.id))
    if reviewer is None:
        if sub.hold_reason != "awaiting_spot_check_reviewer":
            hold(session, sub, "awaiting_spot_check_reviewer", escalate_now=True)
        return None
    task = assignment.create_task(session, sub, reviewer, sub.stage_index, stage, "spot_check", effort)
    sub.hold_reason = None
    audit.record(session, sub.tenant_id, "system:assignment", "task.assigned", "review_task", task.id,
                 {"submission_id": sub.id, "reviewer_id": reviewer.id, "stage": "Spot-check", "kind": "spot_check",
                  "effort_minutes": effort})
    return task


def assign_pending(session: Session, tenant_id: str | None = None) -> int:
    """Retry items queued for capacity. Run after each decision and periodically by the worker."""
    q = select(Submission).where(
        Submission.status.in_(["in_review", "spot_check"]),
        Submission.hold_reason.in_(["awaiting_capacity", "awaiting_spot_check_reviewer"]),
    ).order_by(Submission.created_at)
    if tenant_id:
        q = q.where(Submission.tenant_id == tenant_id)
    assigned = 0
    for sub in session.scalars(q).all():
        task = assign_spot_check(session, sub) if sub.status == "spot_check" else assign_stage(session, sub)
        if task is not None:
            assigned += 1
    return assigned


def reassign_open_tasks(session: Session, reviewer_id: str, actor: str, reason: str) -> list[dict]:
    """Cancel a reviewer's open tasks and route each item to someone else (used when a reviewer
    is deactivated or loses the reviewer role). The caller must make them ineligible first."""
    moved = []
    tasks = session.scalars(
        select(ReviewTask).where(ReviewTask.reviewer_id == reviewer_id, ReviewTask.status == "assigned")
        .order_by(ReviewTask.assigned_at)
    ).all()
    for task in tasks:
        task.status = "cancelled"
        task.completed_at = utcnow()
        sub = session.get(Submission, task.submission_id)
        audit.record(session, task.tenant_id, actor, "task.reassigned", "review_task", task.id,
                     {"submission_id": sub.id, "from_reviewer_id": reviewer_id, "reason": reason})
        # Unscored canaries move with the item so the next reviewer is measured on them instead.
        for c in session.scalars(select(Canary).where(Canary.scored_task_id == task.id, Canary.caught.is_(None))).all():
            c.scored_task_id = None
            c.scored_reviewer_id = None
        session.flush()
        new_task = None
        if task.kind == "spot_check" and sub.status == "spot_check":
            new_task = assign_spot_check(session, sub)
        elif task.kind == "primary" and sub.status == "in_review" and task.stage == sub.stage_index:
            new_task = assign_stage(session, sub)
        moved.append({"task_id": task.id, "submission_id": sub.id,
                      "new_reviewer_id": new_task.reviewer_id if new_task else None,
                      "status": "reassigned" if new_task else (sub.hold_reason or sub.status)})
    return moved


# --- Decisions --------------------------------------------------------------------------


def _last_primary_decision(session: Session, sub: Submission) -> Decision | None:
    return session.scalars(
        select(Decision).join(ReviewTask, ReviewTask.id == Decision.review_task_id)
        .where(ReviewTask.submission_id == sub.id, ReviewTask.kind == "primary")
        .order_by(Decision.decided_at.desc(), Decision.id)
    ).first()


def on_decision(session: Session, task: ReviewTask, decision: Decision) -> None:
    sub = session.get(Submission, task.submission_id)
    wf = workflow_of(session, sub)
    stages = stages_of(wf)
    policy = assurance.policy_for(wf)

    if task.kind == "primary":
        if decision.outcome == "approve" and sub.stage_index + 1 < len(stages):
            sub.stage_index += 1
            audit.record(session, sub.tenant_id, "system:workflow", "submission.stage_advanced", "submission", sub.id,
                         {"stage": stages[sub.stage_index].get("name"), "index": sub.stage_index})
            assign_stage(session, sub)
            return
        prelim = assurance.compute(session, sub, wf)
        force = prelim.below_threshold and policy["below_action"] == "second_review"
        if force or _sample_for_spot_check(session, sub, wf, task.reviewer_id):
            transition(session, sub, "spot_check", "system:workflow", forced_by_threshold=force)
            assign_spot_check(session, sub)
            return
        finalise(session, sub, decision.outcome, decision.reason)
        return

    # Blind second review: compare with the primary decision before anything is released.
    primary = _last_primary_decision(session, sub)
    if primary and primary.outcome == decision.outcome:
        audit.record(session, sub.tenant_id, "system:workflow", "spot_check.agreed", "submission", sub.id,
                     {"outcome": decision.outcome})
        finalise(session, sub, primary.outcome, primary.reason)
    else:
        assurance.compute(session, sub, wf)
        audit.record(session, sub.tenant_id, "system:workflow", "spot_check.disagreed", "submission", sub.id,
                     {"primary": primary.outcome if primary else None, "spot_check": decision.outcome})
        hold(session, sub, "spot_check_disagreement", escalate_now=True)


def finalise(session: Session, sub: Submission, outcome: str, reason: str, actor: str = "system:workflow",
             override_threshold: bool = False) -> None:
    wf = workflow_of(session, sub)
    policy = assurance.policy_for(wf)
    score = assurance.compute(session, sub, wf)
    if score.below_threshold and policy["below_action"] == "block" and not override_threshold:
        hold(session, sub, "below_threshold", escalate_now=True)
        return
    try:
        release(session, sub, outcome, reason, actor, score_flagged=score.below_threshold)
    except ReleaseCheckFailed as exc:
        audit.record(session, sub.tenant_id, "system:workflow", "release.check_failed", "submission", sub.id, {"error": str(exc)})
        hold(session, sub, "release_check_failed", escalate_now=True)


def release_check(session: Session, sub: Submission) -> None:
    """The approved content must be exactly the original submission: no canary can reach a client."""
    primary = [d for d in sub.documents if d.role == "primary"]
    if len(primary) != 1:
        raise ReleaseCheckFailed("expected exactly one primary document")
    doc: Document = primary[0]
    stored = get_storage().get(doc.original_file_uri)
    if hashlib.sha256(stored).hexdigest() != doc.sha256:
        raise ReleaseCheckFailed("original file hash mismatch")
    if ingestion.content_hash(session, doc.id) != sub.content_hash:
        raise ReleaseCheckFailed("normalised content hash mismatch")


def release(session: Session, sub: Submission, outcome: str, reason: str, actor: str, score_flagged: bool = False) -> None:
    from services import evidence

    release_check(session, sub)
    transition(session, sub, OUTCOME_STATUS[outcome], actor, outcome=outcome)
    sub.final_outcome = OUTCOME_STATUS[outcome]
    sub.final_reason = reason
    sub.released_at = utcnow()
    sub.hold_reason = None
    for t in session.scalars(select(ReviewTask).where(ReviewTask.submission_id == sub.id, ReviewTask.status == "assigned")).all():
        t.status = "cancelled"
    audit.record(session, sub.tenant_id, actor, "submission.released", "submission", sub.id,
                 {"outcome": sub.final_outcome, "content_hash": sub.content_hash, "below_threshold": score_flagged})
    evidence.generate_pack(session, sub)
    webhooks.emit(session, sub, "submission.decided")
    assign_pending(session, sub.tenant_id)


def resolve(session: Session, sub: Submission, outcome: str, reason: str, actor: str) -> None:
    """Oversight lead resolves a held item (disagreement, threshold block)."""
    if sub.status not in ("in_review", "spot_check") or sub.hold_reason not in (
        "spot_check_disagreement", "below_threshold", "awaiting_spot_check_reviewer"
    ):
        raise TransitionError("submission is not held for oversight resolution")
    audit.record(session, sub.tenant_id, actor, "submission.resolved", "submission", sub.id,
                 {"outcome": outcome, "hold_reason": sub.hold_reason})
    finalise(session, sub, outcome, reason, actor, override_threshold=True)


def cancel(session: Session, sub: Submission, actor: str) -> None:
    if sub.status in FINAL:
        raise TransitionError(f"submission already {sub.status}")
    transition(session, sub, "cancelled", actor)
    sub.hold_reason = None
    for t in session.scalars(select(ReviewTask).where(ReviewTask.submission_id == sub.id, ReviewTask.status == "assigned")).all():
        t.status = "cancelled"


def check_slas(session: Session) -> int:
    """Escalate overdue review tasks. Run periodically by the worker."""
    now = utcnow()
    overdue = session.scalars(
        select(ReviewTask).where(ReviewTask.status == "assigned", ReviewTask.due_at.is_not(None),
                                 ReviewTask.due_at < now, ReviewTask.sla_escalated.is_(False))
    ).all()
    for task in overdue:
        task.sla_escalated = True
        sub = session.get(Submission, task.submission_id)
        escalate(session, sub, "sla_breached", stage=task.stage_name, task_id=task.id, reviewer_id=task.reviewer_id)
    return len(overdue)
