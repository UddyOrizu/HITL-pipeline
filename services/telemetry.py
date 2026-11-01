"""Telemetry collector and derived measures. Review-session telemetry only, computed deterministically."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import (
    Comment,
    FlagAcknowledgement,
    ReviewTask,
    RiskFlag,
    Section,
    Span,
    Submission,
    TelemetryEvent,
    utcnow,
)

EVENT_TYPES = {
    "document_opened", "section_in_view", "scroll_depth", "flag_opened", "comment_added", "edit_made",
    "focus_lost", "focus_gained", "decision_submitted", "heartbeat",
}
IDLE_CUTOFF_SECONDS = 30
COMPLEXITY = {"low": 0.8, "medium": 1.0, "high": 1.25}
MIN_SECTION_DWELL = 1.5

DEFAULT_POLICY = {"min_reading_ratio": 0.3, "min_coverage": 0.6, "action": "warn"}


def ingest(session: Session, task: ReviewTask, events: list[dict]) -> int:
    """Store a batch from the reviewer client. Returns number stored."""
    now = utcnow()
    stored = 0
    for e in events[:1000]:
        etype = e.get("type")
        if etype not in EVENT_TYPES:
            continue
        ts = e.get("client_ts")
        try:
            client_ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).replace(tzinfo=None) if ts else now
        except ValueError:
            client_ts = now
        # Clamp clock skew: client time can be off, but not in the future or before the task existed.
        client_ts = min(max(client_ts, task.assigned_at - timedelta(minutes=1)), now + timedelta(seconds=5))
        value = e.get("value")
        session.add(
            TelemetryEvent(
                tenant_id=task.tenant_id, review_task_id=task.id, type=etype,
                span_id=e.get("span_id"), section_id=e.get("section_id"),
                value=float(value) if isinstance(value, int | float) else None, client_ts=client_ts, created_at=now,
            )
        )
        stored += 1
    if stored and task.opened_at is None:
        task.opened_at = now
    session.flush()
    return stored


def _visible_spans(session: Session, task: ReviewTask) -> list[tuple[Section, Span]]:
    from services.review import review_copy_spans

    return review_copy_spans(session, task)


def summarise(session: Session, task: ReviewTask) -> dict:
    sub = session.get(Submission, task.submission_id)
    events = session.scalars(
        select(TelemetryEvent).where(TelemetryEvent.review_task_id == task.id).order_by(TelemetryEvent.client_ts)
    ).all()

    # Active time: sum gaps between consecutive events, ignoring idle gaps and time while focus was lost.
    active = 0.0
    focused = True
    focus_lost = 0
    prev: datetime | None = None
    for e in events:
        if prev is not None and focused:
            gap = (e.client_ts - prev).total_seconds()
            if 0 < gap <= IDLE_CUTOFF_SECONDS:
                active += gap
        if e.type == "focus_lost":
            focused = False
            focus_lost += 1
        elif e.type in ("focus_gained", "document_opened"):
            focused = True
        prev = e.client_ts

    pairs = _visible_spans(session, task)
    section_ids = {sec.id for sec, _ in pairs}
    words = sum(len(sp.text.split()) for _, sp in pairs)
    from services.config import get_settings

    wpm = get_settings().words_per_minute
    expected = max(20.0, words / wpm * 60 * COMPLEXITY.get(sub.risk_tier if sub else "medium", 1.0))

    dwell: dict[str, float] = {}
    for e in events:
        if e.type == "section_in_view" and e.section_id in section_ids:
            dwell[e.section_id] = dwell.get(e.section_id, 0) + (e.value or 0)
    viewed = sum(1 for v in dwell.values() if v >= MIN_SECTION_DWELL)
    scroll = max([e.value or 0 for e in events if e.type == "scroll_depth"], default=0)

    visible_span_ids = {sp.id for _, sp in pairs}
    flags = session.scalars(
        select(RiskFlag).where(RiskFlag.submission_id == task.submission_id, RiskFlag.span_id.in_(visible_span_ids))
    ).all()
    acks = {
        a.risk_flag_id: a
        for a in session.scalars(select(FlagAcknowledgement).where(FlagAcknowledgement.review_task_id == task.id)).all()
    }
    opened = sum(1 for f in flags if f.id in acks and acks[f.id].opened_at)
    acked = sum(1 for f in flags if f.id in acks and acks[f.id].acknowledged_at)
    comments = session.scalar(select(func.count()).select_from(Comment).where(Comment.review_task_id == task.id, Comment.kind == "comment")) or 0
    edits = session.scalar(select(func.count()).select_from(Comment).where(Comment.review_task_id == task.id, Comment.kind == "edit")) or 0

    return {
        "active_seconds": round(active, 1),
        "expected_seconds": round(expected, 1),
        "reading_ratio": round(min(active / expected, 5.0), 3) if expected else 0.0,
        "word_count": words,
        "sections_total": len(section_ids),
        "sections_viewed": viewed,
        "coverage": round(viewed / len(section_ids), 3) if section_ids else 1.0,
        "max_scroll_depth": round(scroll, 3),
        "flags_total": len(flags),
        "flags_opened": opened,
        "flags_acknowledged": acked,
        "flag_engagement": round(acked / len(flags), 3) if flags else 1.0,
        "focus_lost_count": focus_lost,
        "comments": comments,
        "edits": edits,
        "event_count": len(events),
    }


def check_policy(policy: dict | None, summary: dict) -> tuple[str, list[str]]:
    """Returns (action, reasons). action is 'ok', 'warn', 'block' or 'flag'."""
    p = {**DEFAULT_POLICY, **(policy or {})}
    reasons = []
    if summary["reading_ratio"] < p["min_reading_ratio"]:
        reasons.append(
            f"Active reading time {summary['active_seconds']:.0f}s is below the minimum "
            f"({p['min_reading_ratio']:.0%} of the expected {summary['expected_seconds']:.0f}s)."
        )
    if summary["coverage"] < p["min_coverage"]:
        reasons.append(
            f"Only {summary['sections_viewed']} of {summary['sections_total']} sections were viewed "
            f"(minimum {p['min_coverage']:.0%})."
        )
    if not reasons:
        return "ok", []
    return p["action"], reasons
