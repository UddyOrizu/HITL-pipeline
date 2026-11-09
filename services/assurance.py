"""Assurance service: Review Assurance Score (0-100) and oversight dashboard aggregates."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    AssuranceScore,
    Canary,
    Decision,
    ReviewTask,
    Submission,
    Team,
    User,
    Workflow,
    utcnow,
)
from services import audit
from services.assignment import caps_for

DEFAULT_WEIGHTS = {
    "reading_coverage": 0.30,
    "risk_engagement": 0.25,
    "reviewer_reliability": 0.20,
    "load": 0.10,
    "independent_check": 0.15,
}
DEFAULT_POLICY = {"min_score": 60, "below_action": "release_flag"}  # block | second_review | release_flag


def policy_for(workflow: Workflow) -> dict:
    return {**DEFAULT_POLICY, **{k: v for k, v in (workflow.assurance_policy or {}).items() if v is not None}}


def load_component(position: float) -> float:
    """Full marks under half the cap, falling to zero at or over the cap."""
    if position <= 0.5:
        return 1.0
    return max(0.0, 1.0 - (position - 0.5) * 2)


def task_components(summary: dict) -> dict:
    reading = 0.5 * summary.get("coverage", 0) + 0.5 * min(1.0, summary.get("reading_ratio", 0))
    return {
        "reading_coverage": round(reading, 4),
        "risk_engagement": round(summary.get("flag_engagement", 1.0), 4),
        "reviewer_reliability": round(summary.get("reliability", 0.5), 4),
        "load": round(load_component(summary.get("load_position", 0)), 4),
    }


def compute(session: Session, submission: Submission, workflow: Workflow, persist: bool = True) -> AssuranceScore:
    weights = {**DEFAULT_WEIGHTS, **(policy_for(workflow).get("weights") or {})}
    rows = session.execute(
        select(ReviewTask, Decision)
        .join(Decision, Decision.review_task_id == ReviewTask.id)
        .where(ReviewTask.submission_id == submission.id)
        .order_by(Decision.decided_at)
    ).all()
    primary = [(t, d) for t, d in rows if t.kind == "primary"]
    spot = [(t, d) for t, d in rows if t.kind == "spot_check"]

    comps: dict[str, float] = {}
    if primary:
        per_task = [task_components(d.telemetry_summary or {}) for _, d in primary]
        for key in ("reading_coverage", "risk_engagement", "reviewer_reliability", "load"):
            comps[key] = round(statistics.fmean(c[key] for c in per_task), 4)
    else:
        comps = {k: 0.0 for k in ("reading_coverage", "risk_engagement", "reviewer_reliability", "load")}

    sampled = bool(spot)
    if sampled:
        final_primary = primary[-1][1].outcome if primary else None
        comps["independent_check"] = 1.0 if spot[-1][1].outcome == final_primary else 0.0
        active = weights
    else:
        # Not sampled: redistribute the independent-check weight across the other components.
        active = {k: v for k, v in weights.items() if k != "independent_check"}
        total = sum(active.values())
        active = {k: v / total for k, v in active.items()}

    score = round(100 * sum(active[k] * comps[k] for k in active), 1)
    policy = policy_for(workflow)
    breakdown = {
        k: {"value": comps.get(k), "weight": round(active.get(k, 0), 4), "points": round(100 * active.get(k, 0) * comps.get(k, 0), 1)}
        for k in DEFAULT_WEIGHTS
    }
    breakdown["independent_check"]["sampled"] = sampled
    result = AssuranceScore(
        tenant_id=submission.tenant_id, submission_id=submission.id, score=score, components=breakdown,
        below_threshold=score < policy["min_score"], computed_at=utcnow(),
    )
    if persist:
        session.add(result)
        session.flush()
        audit.record(
            session, submission.tenant_id, "system:assurance", "assurance.scored", "submission", submission.id,
            {"score": score, "below_threshold": result.below_threshold, "min_score": policy["min_score"]},
        )
    return result


def latest(session: Session, submission_id: str) -> AssuranceScore | None:
    return session.scalars(
        select(AssuranceScore).where(AssuranceScore.submission_id == submission_id).order_by(AssuranceScore.computed_at.desc(), AssuranceScore.id)
    ).first()


def score_payload(score: AssuranceScore | None) -> dict | None:
    if score is None:
        return None
    return {
        "score": score.score,
        "below_threshold": score.below_threshold,
        "components": score.components,
        "computed_at": score.computed_at.isoformat() + "Z",
    }


# --- Dashboard ---------------------------------------------------------------------------


def _rate(caught: int, total: int) -> float | None:
    return round(caught / total, 3) if total else None


def dashboard(session: Session, tenant_id: str, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)
    users = {u.id: u for u in session.scalars(select(User).where(User.tenant_id == tenant_id)).all()}
    teams = {t.id: t for t in session.scalars(select(Team).where(Team.tenant_id == tenant_id)).all()}
    subs = {s.id: s for s in session.scalars(select(Submission).where(Submission.tenant_id == tenant_id, Submission.created_at >= since)).all()}
    workflows = {w.id: w for w in session.scalars(select(Workflow).where(Workflow.tenant_id == tenant_id)).all()}

    # Catch rates.
    canaries = session.scalars(
        select(Canary).where(Canary.tenant_id == tenant_id, Canary.caught.is_not(None), Canary.revealed_at >= since)
    ).all()
    by_reviewer: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_team: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_workflow: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_difficulty: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_week: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in canaries:
        hit = 1 if c.caught else 0
        for bucket, key in (
            (by_reviewer, c.scored_reviewer_id),
            (by_difficulty, c.difficulty),
            (by_week, (c.revealed_at - timedelta(days=c.revealed_at.weekday())).date().isoformat()),
        ):
            bucket[key][0] += hit
            bucket[key][1] += 1
        sub = subs.get(c.submission_id) or session.get(Submission, c.submission_id)
        if sub:
            wf = workflows.get(sub.workflow_id)
            by_workflow[wf.name if wf else sub.workflow_id][0] += hit
            by_workflow[wf.name if wf else sub.workflow_id][1] += 1
        user = users.get(c.scored_reviewer_id)
        for tid in (user.team_ids if user else []) or []:
            by_team[teams[tid].name if tid in teams else tid][0] += hit
            by_team[teams[tid].name if tid in teams else tid][1] += 1

    def table(d, label_fn=lambda k: k):
        return [{"key": label_fn(k), "caught": v[0], "total": v[1], "rate": _rate(*v)} for k, v in sorted(d.items(), key=lambda kv: str(kv[0]))]

    # Review time and fast approvals.
    decisions = session.execute(
        select(Decision, ReviewTask).join(ReviewTask, ReviewTask.id == Decision.review_task_id)
        .where(Decision.tenant_id == tenant_id, Decision.decided_at >= since)
    ).all()
    active = [d.telemetry_summary.get("active_seconds", 0) for d, _ in decisions if d.telemetry_summary]
    expected = [d.telemetry_summary.get("expected_seconds", 0) for d, _ in decisions if d.telemetry_summary]
    approvals = [d for d, _ in decisions if d.outcome == "approve"]
    fast = [d for d in approvals if (d.telemetry_summary or {}).get("reading_ratio", 1) < 0.3]

    # Spot-check disagreement.
    spot_rows = [(d, t) for d, t in decisions if t.kind == "spot_check"]
    disagreements = 0
    for d, t in spot_rows:
        primary = session.execute(
            select(Decision).join(ReviewTask, ReviewTask.id == Decision.review_task_id)
            .where(ReviewTask.submission_id == t.submission_id, ReviewTask.kind == "primary")
            .order_by(Decision.decided_at.desc())
        ).scalars().first()
        if primary and primary.outcome != d.outcome:
            disagreements += 1

    # Capacity today per team.
    from services.assignment import load_minutes

    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    default_cap = max((caps_for(w)["daily_minutes"] for w in workflows.values() if w.is_current), default=240)
    capacity = []
    for team in teams.values():
        members = [u for u in users.values() if team.id in (u.team_ids or []) and "reviewer" in (u.roles or []) and u.active]
        used = sum(load_minutes(session, u.id, today) for u in members)
        cap = team.daily_ceiling_minutes or default_cap * len(members)
        capacity.append({"team": team.name, "reviewers": len(members), "used_minutes": round(used, 1), "capacity_minutes": cap,
                         "utilisation": round(used / cap, 3) if cap else None})

    # Scores.
    latest_scores = {}
    for s in session.scalars(
        select(AssuranceScore).where(AssuranceScore.tenant_id == tenant_id, AssuranceScore.computed_at >= since).order_by(AssuranceScore.computed_at)
    ).all():
        latest_scores[s.submission_id] = s
    buckets = {f"{i}-{i + 9}": 0 for i in range(0, 100, 10)}
    for s in latest_scores.values():
        i = min(90, int(s.score // 10) * 10)
        buckets[f"{i}-{i + 9}"] += 1
    released_below = [
        sid for sid, s in latest_scores.items()
        if s.below_threshold and sid in subs and subs[sid].status in ("approved", "rejected", "changes_requested")
    ]

    status_counts: dict[str, int] = defaultdict(int)
    for s in subs.values():
        status_counts[s.status] += 1

    return {
        "window_days": days,
        "catch_rate": {
            "overall": _rate(sum(1 for c in canaries if c.caught), len(canaries)),
            "by_reviewer": table(by_reviewer, lambda k: users[k].name or users[k].email if k in users else k),
            "by_team": table(by_team),
            "by_workflow": table(by_workflow),
            "by_difficulty": table(by_difficulty),
            "by_week": table(by_week),
        },
        "review_time": {
            "median_active_seconds": round(statistics.median(active), 1) if active else None,
            "median_expected_seconds": round(statistics.median(expected), 1) if expected else None,
            "approvals": len(approvals),
            "fast_approvals": len(fast),
            "fast_approval_share": _rate(len(fast), len(approvals)),
        },
        "capacity": capacity,
        "volume": {
            "submissions": len(subs),
            "by_status": dict(status_counts),
            "deferred": status_counts.get("deferred", 0),
            "awaiting_capacity": sum(1 for s in subs.values() if s.hold_reason == "awaiting_capacity"),
            "escalated": sum(1 for s in subs.values() if s.escalated),
        },
        "spot_checks": {"total": len(spot_rows), "disagreements": disagreements, "disagreement_rate": _rate(disagreements, len(spot_rows))},
        "scores": {
            "distribution": buckets,
            "median": round(statistics.median([s.score for s in latest_scores.values()]), 1) if latest_scores else None,
            "released_below_threshold": len(released_below),
        },
    }
