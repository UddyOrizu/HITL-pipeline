"""Operational reports: throughput and turnaround, decisions, SLAs, assurance scores, and reviewer performance.

All figures are computed from the database for a date range. Times are UTC.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import AssuranceScore, Canary, Decision, ReviewTask, SourceApp, Submission, User, Workflow, utcnow

FINAL_DECISIONS = ("approved", "rejected", "changes_requested")


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    i = min(len(v) - 1, max(0, round(q * (len(v) - 1))))
    return round(v[i], 2)


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def bucket(dt: datetime, interval: str) -> str:
    if interval == "day":
        return dt.strftime("%Y-%m-%d")
    if interval == "week":
        monday = dt - timedelta(days=dt.weekday())
        return monday.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m")


def _buckets(start: datetime, end: datetime, interval: str) -> list[str]:
    out, cur = [], start
    step = timedelta(days=1)
    while cur < end and len(out) < 2000:
        b = bucket(cur, interval)
        if not out or out[-1] != b:
            out.append(b)
        cur += step
    return out


def _filtered_subs(db: Session, tenant_id: str, workflow_id: str | None, source_app_id: str | None):
    q = select(Submission).where(Submission.tenant_id == tenant_id)
    if workflow_id:
        ids = db.scalars(select(Workflow.id).where(Workflow.tenant_id == tenant_id, Workflow.family_id == workflow_id)).all()
        q = q.where(Submission.workflow_id.in_(ids or ["-"]))
    if source_app_id:
        q = q.where(Submission.source_app_id == source_app_id)
    return q


def overview(db: Session, tenant_id: str, start: datetime, end: datetime, interval: str = "day",
             workflow_id: str | None = None, source_app_id: str | None = None) -> dict:
    base = _filtered_subs(db, tenant_id, workflow_id, source_app_id)
    received = db.scalars(base.where(Submission.created_at >= start, Submission.created_at < end)).all()
    decided = db.scalars(base.where(Submission.released_at >= start, Submission.released_at < end,
                                    Submission.final_outcome.in_(FINAL_DECISIONS))).all()
    workflows = {w.id: w for w in db.scalars(select(Workflow).where(Workflow.tenant_id == tenant_id)).all()}
    apps = {a.id: a for a in db.scalars(select(SourceApp).where(SourceApp.tenant_id == tenant_id)).all()}

    turnaround = {s.id: (s.released_at - s.created_at).total_seconds() / 3600 for s in decided}
    outcomes = defaultdict(int)
    for s in decided:
        outcomes[s.final_outcome] += 1

    scores: dict[str, AssuranceScore] = {}
    if decided:
        for sc in db.scalars(select(AssuranceScore).where(AssuranceScore.submission_id.in_([s.id for s in decided]))
                             .order_by(AssuranceScore.computed_at)).all():
            scores[sc.submission_id] = sc

    sub_ids = [s.id for s in received]
    tasks_in_range = db.scalars(select(ReviewTask).where(
        ReviewTask.tenant_id == tenant_id, ReviewTask.due_at >= start, ReviewTask.due_at < end)).all()
    if workflow_id or source_app_id:
        allowed = {s.id for s in db.scalars(base).all()}
        tasks_in_range = [t for t in tasks_in_range if t.submission_id in allowed]
    breaches = [t for t in tasks_in_range if t.sla_escalated or (t.completed_at and t.due_at and t.completed_at > t.due_at)]

    series_keys = _buckets(start, end, interval)
    series = {k: {"period": k, "received": 0, "decided": 0, "approved": 0, "rejected": 0, "changes_requested": 0,
                  "_turnaround": []} for k in series_keys}
    for s in received:
        k = bucket(s.created_at, interval)
        if k in series:
            series[k]["received"] += 1
    for s in decided:
        k = bucket(s.released_at, interval)
        if k in series:
            series[k]["decided"] += 1
            series[k][s.final_outcome] += 1
            series[k]["_turnaround"].append(turnaround[s.id])
    series_out = []
    for k in series_keys:
        row = series[k]
        row["median_turnaround_hours"] = _median(row.pop("_turnaround"))
        series_out.append(row)

    def breakdown(key_fn, label_fn) -> list[dict]:
        acc: dict[str, dict] = {}
        for s in received:
            k = key_fn(s)
            r = acc.setdefault(k, {"key": k, "label": label_fn(k), "received": 0, "decided": 0, "approved": 0,
                                   "_t": [], "_scores": []})
            r["received"] += 1
        for s in decided:
            k = key_fn(s)
            r = acc.setdefault(k, {"key": k, "label": label_fn(k), "received": 0, "decided": 0, "approved": 0,
                                   "_t": [], "_scores": []})
            r["decided"] += 1
            r["approved"] += s.final_outcome == "approved"
            r["_t"].append(turnaround[s.id])
            if s.id in scores:
                r["_scores"].append(scores[s.id].score)
        out = []
        for r in acc.values():
            r["approval_rate"] = _rate(r["approved"], r["decided"])
            r["median_turnaround_hours"] = _median(r.pop("_t"))
            r["median_score"] = _median(r.pop("_scores"))
            out.append(r)
        return sorted(out, key=lambda r: -r["received"])

    def wf_key(s):
        w = workflows.get(s.workflow_id)
        return w.family_id if w else "unknown"

    wf_names = {w.family_id: w.name for w in workflows.values() if w.is_current}
    channels: dict[str, int] = defaultdict(int)
    if decided:
        for d in db.scalars(select(Decision).where(Decision.submission_id.in_([s.id for s in decided]))).all():
            channels[d.channel] += 1

    score_values = [sc.score for sc in scores.values()]
    held_now = db.scalars(base.where(Submission.hold_reason.is_not(None))).all()
    return {
        "from": start.isoformat() + "Z", "to": end.isoformat() + "Z", "interval": interval,
        "kpis": {
            "received": len(received),
            "decided": len(decided),
            "approved": outcomes["approved"],
            "rejected": outcomes["rejected"],
            "changes_requested": outcomes["changes_requested"],
            "approval_rate": _rate(outcomes["approved"], len(decided)),
            "cancelled": sum(1 for s in received if s.status == "cancelled"),
            "deferred": sum(1 for s in received if s.status == "deferred"),
            "in_progress": sum(1 for s in received if s.status in ("received", "processing", "in_review", "spot_check")),
            "median_turnaround_hours": _median(list(turnaround.values())),
            "p90_turnaround_hours": _pct(list(turnaround.values()), 0.9),
            "sla_tasks_due": len(tasks_in_range),
            "sla_breaches": len(breaches),
            "sla_breach_rate": _rate(len(breaches), len(tasks_in_range)),
            "escalated": sum(1 for s in received if s.escalated),
            "held_now": len(held_now),
            "median_score": _median(score_values),
            "below_threshold": sum(1 for sc in scores.values() if sc.below_threshold),
            "submissions_with_ids": len(sub_ids),
        },
        "series": series_out,
        "by_workflow": breakdown(wf_key, lambda k: wf_names.get(k, "Unknown workflow")),
        "by_source_app": breakdown(lambda s: s.source_app_id, lambda k: apps[k].name if k in apps else "Unknown"),
        "by_risk_tier": breakdown(lambda s: s.risk_tier, lambda k: k),
        "by_origin": breakdown(lambda s: s.content_origin, lambda k: k),
        "by_channel": [{"channel": k, "decisions": v} for k, v in sorted(channels.items())],
        "held_by_reason": _count(s.hold_reason for s in held_now),
    }


def _count(values) -> list[dict]:
    acc: dict[str, int] = defaultdict(int)
    for v in values:
        acc[v] += 1
    return [{"key": k, "count": n} for k, n in sorted(acc.items(), key=lambda x: -x[1])]


def _empty_reviewer() -> dict:
    return {"tasks": 0, "primary": 0, "spot_checks": 0, "approvals": 0, "warnings_confirmed": 0, "warnings_flagged": 0,
            "_active": [], "_ratio": [], "_coverage": [], "_flags": [], "sla_breaches": 0, "channels": defaultdict(int)}


def reviewers(db: Session, tenant_id: str, start: datetime, end: datetime) -> list[dict]:
    users = {u.id: u for u in db.scalars(select(User).where(User.tenant_id == tenant_id)).all()}
    rows = db.execute(
        select(ReviewTask, Decision).join(Decision, Decision.review_task_id == ReviewTask.id)
        .where(ReviewTask.tenant_id == tenant_id, Decision.decided_at >= start, Decision.decided_at < end)
    ).all()
    acc: dict[str, dict] = {}
    disagreed: dict[str, int] = defaultdict(int)  # spot-checks that disagreed with this reviewer's decision
    for t, d in rows:
        r = acc.setdefault(t.reviewer_id, _empty_reviewer())
        r["tasks"] += 1
        r["primary" if t.kind == "primary" else "spot_checks"] += 1
        r["approvals"] += d.outcome == "approve"
        tel = d.telemetry_summary or {}
        if d.warnings:
            # "flagged": let through by the telemetry policy's flag action; otherwise the reviewer confirmed the
            # warning (decisions made before the distinction count as confirmed).
            r["warnings_flagged" if tel.get("review_standard") == "flagged" else "warnings_confirmed"] += 1
        r["channels"][d.channel] += 1
        if d.channel == "ui":
            r["_active"].append((tel.get("active_seconds") or 0) / 60)
            r["_ratio"].append(tel.get("reading_ratio") or 0)
            r["_coverage"].append(tel.get("coverage") or 0)
        if tel.get("flags_total"):
            r["_flags"].append(tel.get("flag_engagement") or 0)
        if t.due_at and (t.sla_escalated or (t.completed_at and t.completed_at > t.due_at)):
            r["sla_breaches"] += 1
        if t.kind == "spot_check":
            primary = db.scalars(select(Decision).join(ReviewTask, ReviewTask.id == Decision.review_task_id)
                                 .where(ReviewTask.submission_id == t.submission_id, ReviewTask.kind == "primary")
                                 .order_by(Decision.decided_at.desc())).first()
            if primary and primary.outcome != d.outcome:
                prev = db.get(ReviewTask, primary.review_task_id)
                if prev:
                    disagreed[prev.reviewer_id] += 1

    canaries = db.scalars(select(Canary).where(Canary.tenant_id == tenant_id, Canary.caught.is_not(None),
                                               Canary.revealed_at >= start, Canary.revealed_at < end)).all()
    caught: dict[str, list[bool]] = defaultdict(list)
    for c in canaries:
        if c.scored_reviewer_id:
            caught[c.scored_reviewer_id].append(bool(c.caught))
    open_tasks: dict[str, int] = defaultdict(int)
    overdue: dict[str, int] = defaultdict(int)
    now = utcnow()
    for t in db.scalars(select(ReviewTask).where(ReviewTask.tenant_id == tenant_id, ReviewTask.status == "assigned")).all():
        open_tasks[t.reviewer_id] += 1
        if t.due_at and t.due_at < now:
            overdue[t.reviewer_id] += 1

    out = []
    for uid in set(acc) | {u.id for u in users.values() if "reviewer" in (u.roles or []) and u.active}:
        r = acc.get(uid) or _empty_reviewer()
        u = users.get(uid)
        c = caught.get(uid, [])
        out.append({
            "reviewer_id": uid, "name": (u.name or u.email) if u else uid, "email": u.email if u else None,
            "active": bool(u and u.active),
            "tasks": r["tasks"], "primary": r["primary"], "spot_checks": r["spot_checks"],
            "approval_rate": _rate(r["approvals"], r["tasks"]),
            "median_active_minutes": _median(r["_active"]),
            "median_reading_ratio": _median(r["_ratio"]),
            "mean_coverage": round(sum(r["_coverage"]) / len(r["_coverage"]), 3) if r["_coverage"] else None,
            "flag_engagement": round(sum(r["_flags"]) / len(r["_flags"]), 3) if r["_flags"] else None,
            "warnings_confirmed": r["warnings_confirmed"], "warnings_flagged": r["warnings_flagged"],
            "canaries_scored": len(c), "catch_rate": _rate(sum(c), len(c)),
            "spot_check_disagreements": disagreed.get(uid, 0),
            "sla_breaches": r["sla_breaches"],
            "open_tasks": open_tasks.get(uid, 0), "overdue_tasks": overdue.get(uid, 0),
            "channels": dict(r["channels"]),
        })
    return sorted(out, key=lambda x: (-x["tasks"], x["name"]))


def sla(db: Session, tenant_id: str) -> dict:
    """Open tasks that are overdue now, oldest first."""
    now = utcnow()
    users = {u.id: u for u in db.scalars(select(User).where(User.tenant_id == tenant_id)).all()}
    rows = db.execute(select(ReviewTask, Submission).join(Submission, Submission.id == ReviewTask.submission_id)
                      .where(ReviewTask.tenant_id == tenant_id, ReviewTask.status == "assigned",
                             ReviewTask.due_at.is_not(None), ReviewTask.due_at < now)
                      .order_by(ReviewTask.due_at)).all()
    return {"overdue": [{
        "task_id": t.id, "submission_id": s.id, "title": (s.meta or {}).get("title") or s.external_ref,
        "stage": t.stage_name, "kind": t.kind, "reviewer": (users[t.reviewer_id].name or users[t.reviewer_id].email)
        if t.reviewer_id in users else t.reviewer_id, "due_at": t.due_at.isoformat() + "Z",
        "hours_overdue": round((now - t.due_at).total_seconds() / 3600, 1), "escalated": t.sla_escalated,
    } for t, s in rows]}


REVIEWER_COLUMNS = ["name", "email", "tasks", "primary", "spot_checks", "approval_rate", "median_active_minutes",
                    "median_reading_ratio", "mean_coverage", "flag_engagement", "warnings_confirmed", "warnings_flagged",
                    "canaries_scored",
                    "catch_rate", "spot_check_disagreements", "sla_breaches", "open_tasks", "overdue_tasks"]
SERIES_COLUMNS = ["period", "received", "decided", "approved", "rejected", "changes_requested", "median_turnaround_hours"]
SLA_COLUMNS = ["task_id", "submission_id", "title", "stage", "kind", "reviewer", "due_at", "hours_overdue", "escalated"]
OVERVIEW_COLUMNS = ["section", "key", "label", "period", "received", "decided", "approved", "rejected",
                    "changes_requested", "approval_rate", "median_turnaround_hours", "median_score", "decisions",
                    "count", "value"]
BREAKDOWNS = ("by_workflow", "by_source_app", "by_risk_tier", "by_origin")


def overview_csv_rows(data: dict) -> list[dict]:
    """The whole overview as one long-format table: a "section" column says which part each row belongs to
    (kpi, series, by_workflow, by_source_app, by_risk_tier, by_origin, by_channel, held_by_reason)."""
    rows: list[dict] = [{"section": "kpi", "key": k, "label": k, "value": v} for k, v in data["kpis"].items()]
    rows += [{"section": "series", **r} for r in data["series"]]
    for name in BREAKDOWNS:
        rows += [{"section": name, **r} for r in data[name]]
    rows += [{"section": "by_channel", "key": r["channel"], "label": r["channel"], "decisions": r["decisions"]}
             for r in data["by_channel"]]
    rows += [{"section": "held_by_reason", "key": r["key"], "label": r["key"], "count": r["count"]}
             for r in data["held_by_reason"]]
    return rows
