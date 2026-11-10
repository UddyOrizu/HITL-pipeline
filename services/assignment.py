"""Assignment service: effort estimation, volume caps, rotation and conflict rules."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import ReviewTask, Span, Submission, Team, Tenant, User, Workflow, utcnow
from services.config import get_settings

RISK_MULTIPLIER = {"low": 1.0, "medium": 1.3, "high": 1.6}
BASE_MINUTES = 2.0

DEFAULT_CAPS = {
    "daily_minutes": 240,
    "hourly_minutes": 90,
    "team_daily_minutes": None,
    "overflow": "queue",  # queue | escalate | defer
    "max_consecutive_same_source": 3,
}


def caps_for(workflow: Workflow) -> dict:
    return {**DEFAULT_CAPS, **{k: v for k, v in (workflow.cap_policy or {}).items() if v is not None}}


def word_count(session: Session, submission: Submission) -> int:
    doc_ids = [d.id for d in submission.documents if d.role == "primary"]
    texts = session.scalars(
        select(Span.text).where(Span.document_id.in_(doc_ids), Span.review_only.is_(False))
    ).all()
    return sum(len(t.split()) for t in texts)


def estimate_effort(words: int, risk_tier: str) -> float:
    """Review minutes, so ten one-page letters and one 40-page report are not treated as equal."""
    wpm = get_settings().words_per_minute
    return round(BASE_MINUTES + (words / wpm) * RISK_MULTIPLIER.get(risk_tier, 1.3), 1)


def _start_of_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def load_minutes(session: Session, reviewer_id: str, since: datetime, until: datetime | None = None) -> float:
    q = select(func.coalesce(func.sum(ReviewTask.effort_minutes), 0)).where(
        ReviewTask.reviewer_id == reviewer_id, ReviewTask.assigned_at >= since, ReviewTask.status != "cancelled"
    )
    if until is not None:
        q = q.where(ReviewTask.assigned_at < until)
    return float(session.scalar(q) or 0)


def team_load_minutes(session: Session, tenant_id: str, member_ids: list[str], since: datetime) -> float:
    if not member_ids:
        return 0.0
    return float(
        session.scalar(
            select(func.coalesce(func.sum(ReviewTask.effort_minutes), 0)).where(
                ReviewTask.tenant_id == tenant_id, ReviewTask.reviewer_id.in_(member_ids),
                ReviewTask.assigned_at >= since, ReviewTask.status != "cancelled",
            )
        )
        or 0
    )


def team_member_ids(session: Session, tenant_id: str, team_id: str) -> list[str]:
    """Every active member of the team (not just the people eligible for one item)."""
    return [u.id for u in session.scalars(select(User).where(User.tenant_id == tenant_id, User.active.is_(True))).all()
            if team_id in (u.team_ids or [])]


def load_position(session: Session, reviewer_id: str, workflow: Workflow, at: datetime | None = None) -> float:
    """Reviewer's load on the day of `at` as a fraction of their daily cap (used by the assurance score). Only
    tasks assigned that day count, so work assigned later does not inflate an earlier decision's load."""
    caps = caps_for(workflow)
    day = _start_of_day(at or utcnow())
    used = load_minutes(session, reviewer_id, day, day + timedelta(days=1))
    return used / caps["daily_minutes"] if caps["daily_minutes"] else 0.0


def admins_may_review(session: Session, tenant_id: str) -> bool:
    tenant = session.get(Tenant, tenant_id)
    return bool(tenant and (tenant.settings or {}).get("allow_admin_reviewers", False))


def _rotation_blocked(session: Session, reviewer: User, submission: Submission, limit: int) -> bool:
    if not limit:
        return False
    recent = session.execute(
        select(Submission.source_app_id, Submission.meta)
        .join(ReviewTask, ReviewTask.submission_id == Submission.id)
        .where(ReviewTask.reviewer_id == reviewer.id, ReviewTask.status != "cancelled")
        .order_by(ReviewTask.assigned_at.desc())
        .limit(limit)
    ).all()
    if len(recent) < limit:
        return False
    client = (submission.meta or {}).get("client")
    return all(r[0] == submission.source_app_id and (r[1] or {}).get("client") == client for r in recent)


def eligible_reviewers(
    session: Session, submission: Submission, workflow: Workflow, stage: dict, exclude_ids: set[str]
) -> list[User]:
    users = session.scalars(
        select(User).where(User.tenant_id == submission.tenant_id, User.active.is_(True))
    ).all()
    team_id = stage.get("team_id")
    author = ((submission.meta or {}).get("author_email") or "").lower()
    # Separation of duties: unless the tenant allows it, an account holding admin never reviews, even if it
    # was given the reviewer role before the setting was turned off.
    admins_ok = admins_may_review(session, submission.tenant_id)
    out = []
    for u in users:
        if "reviewer" not in (u.roles or []):
            continue
        if "admin" in (u.roles or []) and not admins_ok:
            continue
        if team_id and team_id not in (u.team_ids or []):
            continue
        if u.id in exclude_ids:
            continue
        if author and u.email.lower() == author:  # conflict rule: nobody reviews their own work
            continue
        out.append(u)
    return out


def pick_reviewer(
    session: Session, submission: Submission, workflow: Workflow, stage: dict, effort: float, exclude_ids: set[str]
) -> User | None:
    """Choose a reviewer under their caps, honouring rotation, round-robin by least recent assignment."""
    caps = caps_for(workflow)
    now = utcnow()
    day, hour = _start_of_day(now), now - timedelta(hours=1)
    candidates = eligible_reviewers(session, submission, workflow, stage, exclude_ids)
    if not candidates:
        return None

    # Team ceilings apply per team, to the load of every active member of that team. A stage with a team checks
    # that team; otherwise each candidate is checked against the teams they belong to. A reviewer in no team has
    # no team ceiling (there is no tenant-wide pseudo-team).
    workflow_ceiling = caps.get("team_daily_minutes")
    stage_team = stage.get("team_id")
    team_ok: dict[str, bool] = {}

    def within_team_ceiling(tid: str) -> bool:
        if tid not in team_ok:
            team = session.get(Team, tid)
            ceiling = workflow_ceiling or (team.daily_ceiling_minutes if team else None)
            if not ceiling:
                team_ok[tid] = True
            else:
                members = team_member_ids(session, submission.tenant_id, tid)
                team_ok[tid] = team_load_minutes(session, submission.tenant_id, members, day) + effort <= ceiling
        return team_ok[tid]

    if stage_team:
        if not within_team_ceiling(stage_team):
            return None
    else:
        candidates = [u for u in candidates if all(within_team_ceiling(t) for t in (u.team_ids or []))]

    under_cap = []
    for u in candidates:
        if load_minutes(session, u.id, day) + effort > caps["daily_minutes"]:
            continue
        if load_minutes(session, u.id, hour) + effort > caps["hourly_minutes"]:
            continue
        under_cap.append(u)
    if not under_cap:
        return None

    rotated = [u for u in under_cap if not _rotation_blocked(session, u, submission, caps["max_consecutive_same_source"])]
    pool = rotated or under_cap

    def last_assigned(u: User):
        ts = session.scalar(select(func.max(ReviewTask.assigned_at)).where(ReviewTask.reviewer_id == u.id))
        return (ts or datetime.min, u.id)

    return sorted(pool, key=last_assigned)[0]


def create_task(
    session: Session, submission: Submission, reviewer: User, stage_index: int, stage: dict, kind: str, effort: float
) -> ReviewTask:
    now = utcnow()
    sla = stage.get("sla_hours")
    task = ReviewTask(
        tenant_id=submission.tenant_id, submission_id=submission.id, reviewer_id=reviewer.id, stage=stage_index,
        stage_name=stage.get("name", "Review"), kind=kind, assigned_at=now,
        due_at=now + timedelta(hours=float(sla)) if sla else None, effort_minutes=effort,
    )
    session.add(task)
    session.flush()
    return task


def prior_reviewer_ids(session: Session, submission_id: str) -> set[str]:
    """Reviewers who must not get this item again: everyone with a live or finished task on it, and anyone whose
    task was taken away after an SLA breach (a cancelled task with sla_escalated set)."""
    from sqlalchemy import or_

    return set(
        session.scalars(
            select(ReviewTask.reviewer_id).where(
                ReviewTask.submission_id == submission_id,
                or_(ReviewTask.status != "cancelled", ReviewTask.sla_escalated.is_(True)),
            )
        ).all()
    )


__all__ = [
    "caps_for", "create_task", "eligible_reviewers", "estimate_effort", "load_minutes", "load_position",
    "pick_reviewer", "prior_reviewer_ids", "word_count",
]
