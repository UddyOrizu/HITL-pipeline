"""Reviewer actions: the review copy, flags, comments and decisions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    Canary,
    Comment,
    Decision,
    Document,
    FlagAcknowledgement,
    ReviewTask,
    RiskFlag,
    Section,
    Span,
    Submission,
    User,
    Workflow,
    utcnow,
)
from services import assignment, audit, canary, telemetry, workflow

OUTCOMES = {"approve", "reject", "request_changes"}


class ReviewError(ValueError):
    def __init__(self, message: str, code: str = "invalid", details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def visible_canary(session: Session, task: ReviewTask) -> Canary | None:
    c = canary.canary_for_task(session, task.id)
    return c if c is not None and c.revealed_at is None else None


def review_copy_spans(session: Session, task: ReviewTask) -> list[tuple[Section, Span]]:
    """Original spans plus, for the task the canary is assigned to, the canary span (until revealed)."""
    sub = session.get(Submission, task.submission_id)
    doc_ids = [d.id for d in sub.documents if d.role == "primary"]
    rows = session.execute(
        select(Section, Span).join(Span, Span.section_id == Section.id)
        .where(Span.document_id.in_(doc_ids)).order_by(Section.order, Span.order)
    ).all()
    c = visible_canary(session, task)
    hidden_original = None
    if c is not None:
        cspan = session.get(Span, c.span_id)
        hidden_original = cspan.replaces_span_id if cspan else None
    out = []
    for sec, sp in rows:
        if sp.review_only and (c is None or sp.id != c.span_id):
            continue
        if hidden_original and sp.id == hidden_original:
            continue
        out.append((sec, sp))
    return out


def _task_for(session: Session, user: User, task_id: str) -> ReviewTask:
    task = session.get(ReviewTask, task_id)
    if task is None or task.tenant_id != user.tenant_id or task.reviewer_id != user.id:
        raise ReviewError("task not found", "not_found")
    return task


def queue(session: Session, user: User, kind: str | None = None) -> list[dict]:
    q = select(ReviewTask, Submission).join(Submission, Submission.id == ReviewTask.submission_id).where(
        ReviewTask.tenant_id == user.tenant_id, ReviewTask.reviewer_id == user.id, ReviewTask.status == "assigned"
    )
    if kind:
        q = q.where(ReviewTask.kind == kind)
    out = []
    for t, s in session.execute(q.order_by(ReviewTask.due_at.is_(None), ReviewTask.due_at, ReviewTask.assigned_at)).all():
        wf = session.get(Workflow, s.workflow_id)
        doc = next((d for d in s.documents if d.role == "primary"), None)
        out.append({
            "task_id": t.id, "submission_id": s.id, "kind": t.kind, "stage": t.stage_name,
            "workflow": wf.name if wf else None, "title": (s.meta or {}).get("title") or (doc.filename if doc else s.external_ref),
            "risk_tier": s.risk_tier, "content_origin": s.content_origin, "effort_minutes": t.effort_minutes,
            "assigned_at": t.assigned_at.isoformat() + "Z", "due_at": t.due_at.isoformat() + "Z" if t.due_at else None,
            "opened": t.opened_at is not None, "render_kind": doc.render_kind if doc else "text",
        })
    return out


def history(session: Session, user: User, limit: int = 50) -> list[dict]:
    rows = session.execute(
        select(ReviewTask, Decision).join(Decision, Decision.review_task_id == ReviewTask.id)
        .where(ReviewTask.reviewer_id == user.id).order_by(Decision.decided_at.desc()).limit(limit)
    ).all()
    out = []
    for t, d in rows:
        c = session.scalars(select(Canary).where(Canary.scored_task_id == t.id, Canary.caught.is_not(None))).first()
        out.append({
            "task_id": t.id, "submission_id": t.submission_id, "kind": t.kind, "outcome": d.outcome,
            "decided_at": d.decided_at.isoformat() + "Z", "telemetry": d.telemetry_summary,
            "canary": canary.reveal(session, c) if c else None,
        })
    return out


def task_view(session: Session, user: User, task_id: str) -> dict:
    task = _task_for(session, user, task_id)
    sub = session.get(Submission, task.submission_id)
    wf = session.get(Workflow, sub.workflow_id)
    pairs = review_copy_spans(session, task)
    visible_ids = {sp.id for _, sp in pairs}
    doc = next(d for d in sub.documents if d.role == "primary")

    sections: dict[str, dict] = {}
    for sec, sp in pairs:
        s = sections.setdefault(sec.id, {"id": sec.id, "kind": sec.kind, "title": sec.title, "location": sec.location, "spans": []})
        s["spans"].append({"id": sp.id, "kind": sp.kind, "text": sp.text, "location": sp.location})

    acks = {a.risk_flag_id: a for a in session.scalars(select(FlagAcknowledgement).where(FlagAcknowledgement.review_task_id == task.id)).all()}
    flags = [
        {"id": f.id, "span_id": f.span_id, "category": f.category, "severity": f.severity, "rationale": f.rationale,
         "mandatory": f.mandatory, "opened": bool(acks.get(f.id) and acks[f.id].opened_at),
         "acknowledged": bool(acks.get(f.id) and acks[f.id].acknowledged_at)}
        for f in session.scalars(select(RiskFlag).where(RiskFlag.submission_id == sub.id)).all()
        if f.span_id in visible_ids
    ]
    comments = [
        {"id": c.id, "span_id": c.span_id, "kind": c.kind, "body": c.body, "suggested_text": c.suggested_text,
         "created_at": c.created_at.isoformat() + "Z"}
        for c in session.scalars(select(Comment).where(Comment.review_task_id == task.id).order_by(Comment.created_at)).all()
    ]
    # Earlier stages are visible to later primary stages. Spot-checks are blind: no prior decisions or comments.
    prior = []
    if task.kind == "primary" and task.stage > 0:
        for t, d in session.execute(
            select(ReviewTask, Decision).join(Decision, Decision.review_task_id == ReviewTask.id)
            .where(ReviewTask.submission_id == sub.id, ReviewTask.kind == "primary", ReviewTask.stage < task.stage)
        ).all():
            prior.append({"stage": t.stage_name, "outcome": d.outcome, "reason": d.reason})

    if task.opened_at is None:
        task.opened_at = utcnow()
        audit.record(session, task.tenant_id, f"user:{user.id}", "task.opened", "review_task", task.id, {})

    summary = telemetry.summarise(session, task)
    return {
        "task": {"id": task.id, "kind": task.kind, "stage": task.stage_name, "status": task.status,
                 "due_at": task.due_at.isoformat() + "Z" if task.due_at else None, "effort_minutes": task.effort_minutes},
        "submission": {"id": sub.id, "title": (sub.meta or {}).get("title") or doc.filename, "risk_tier": sub.risk_tier,
                       "content_origin": sub.content_origin, "ai_model": sub.ai_model, "workflow": wf.name,
                       "external_ref": sub.external_ref, "previous_submission_id": sub.previous_submission_id,
                       "reference_documents": [{"id": d.id, "filename": d.filename, "render_kind": d.render_kind}
                                               for d in sub.documents if d.role == "reference"]},
        "document": {"id": doc.id, "filename": doc.filename, "render_kind": doc.render_kind, "sections": list(sections.values())},
        "flags": flags,
        "comments": comments,
        "prior_stages": prior,
        "policy": {**telemetry.DEFAULT_POLICY, **(wf.telemetry_policy or {})},
        "telemetry": summary,
    }


def references(session: Session, user: User, task_id: str) -> list[dict]:
    from services import ingestion

    task = _task_for(session, user, task_id)
    sub = session.get(Submission, task.submission_id)
    out = []
    for d in sub.documents:
        if d.role != "reference":
            continue
        sections: list[dict] = []
        for sec, sp in ingestion.original_spans(session, d.id):
            if not sections or sections[-1]["id"] != sec.id:
                sections.append({"id": sec.id, "kind": sec.kind, "title": sec.title, "spans": []})
            sections[-1]["spans"].append({"id": sp.id, "kind": sp.kind, "text": sp.text})
        out.append({"id": d.id, "filename": d.filename, "render_kind": d.render_kind, "mime_type": d.mime_type,
                    "sections": sections, "available": not d.original_file_uri.startswith("purged:")})
    return out


def reference_file(session: Session, user: User, task_id: str, document_id: str):
    from services.storage import get_storage

    task = _task_for(session, user, task_id)
    doc = session.get(Document, document_id)
    if doc is None or doc.submission_id != task.submission_id or doc.role != "reference":
        raise ReviewError("reference document not found", "not_found")
    if doc.original_file_uri.startswith("purged:"):
        raise ReviewError("this document has been removed under the retention policy", "not_found")
    return doc, get_storage().get(doc.original_file_uri)


def _flag_for(session: Session, task: ReviewTask, flag_id: str) -> RiskFlag:
    flag = session.get(RiskFlag, flag_id)
    if flag is None or flag.submission_id != task.submission_id:
        raise ReviewError("flag not found", "not_found")
    return flag


def _ack_row(session: Session, task: ReviewTask, flag: RiskFlag) -> FlagAcknowledgement:
    row = session.scalars(select(FlagAcknowledgement).where(
        FlagAcknowledgement.review_task_id == task.id, FlagAcknowledgement.risk_flag_id == flag.id)).first()
    if row is None:
        row = FlagAcknowledgement(tenant_id=task.tenant_id, review_task_id=task.id, risk_flag_id=flag.id)
        session.add(row)
    return row


def open_flag(session: Session, user: User, task_id: str, flag_id: str) -> dict:
    task = _task_for(session, user, task_id)
    flag = _flag_for(session, task, flag_id)
    row = _ack_row(session, task, flag)
    row.opened_at = row.opened_at or utcnow()
    session.flush()
    return {"flag_id": flag.id, "opened": True, "acknowledged": row.acknowledged_at is not None}


def acknowledge_flag(session: Session, user: User, task_id: str, flag_id: str) -> dict:
    task = _task_for(session, user, task_id)
    flag = _flag_for(session, task, flag_id)
    row = _ack_row(session, task, flag)
    now = utcnow()
    row.opened_at = row.opened_at or now
    row.acknowledged_at = row.acknowledged_at or now
    session.flush()
    audit.record(session, task.tenant_id, f"user:{user.id}", "flag.acknowledged", "review_task", task.id, {"flag_id": flag.id})
    return {"flag_id": flag.id, "opened": True, "acknowledged": True}


def add_comment(session: Session, user: User, task_id: str, span_id: str | None, body: str, kind: str = "comment",
                suggested_text: str | None = None) -> Comment:
    task = _task_for(session, user, task_id)
    if task.status != "assigned":
        raise ReviewError("task is closed", "conflict")
    if kind not in ("comment", "edit"):
        raise ReviewError("kind must be comment or edit")
    if span_id is not None and span_id not in {sp.id for _, sp in review_copy_spans(session, task)}:
        raise ReviewError("span is not part of this review", "not_found")
    if kind == "edit" and not suggested_text:
        raise ReviewError("an edit needs suggested_text")
    c = Comment(tenant_id=task.tenant_id, review_task_id=task.id, span_id=span_id, kind=kind, body=body or "",
                suggested_text=suggested_text)
    session.add(c)
    session.flush()
    audit.record(session, task.tenant_id, f"user:{user.id}", f"{kind}.added", "review_task", task.id,
                 {"comment_id": c.id, "span_id": span_id})
    return c


def delete_comment(session: Session, user: User, task_id: str, comment_id: str) -> None:
    task = _task_for(session, user, task_id)
    c = session.get(Comment, comment_id)
    if c is None or c.review_task_id != task.id or task.status != "assigned":
        raise ReviewError("comment not found", "not_found")
    session.delete(c)
    audit.record(session, task.tenant_id, f"user:{user.id}", "comment.deleted", "review_task", task.id, {"comment_id": comment_id})


ALL_CHANNELS = ["ui", "api", "mcp"]


def allowed_channels(wf: Workflow) -> list[str]:
    return list((wf.telemetry_policy or {}).get("review_channels") or ALL_CHANNELS)


def submit_decision(session: Session, user: User, task_id: str, outcome: str, reason: str = "",
                    cited_span_ids: list[str] | None = None, confirm_warnings: bool = False, channel: str = "api") -> dict:
    task = _task_for(session, user, task_id)
    cited = list(dict.fromkeys(cited_span_ids or []))
    if task.status != "assigned":
        raise ReviewError("task is already closed", "conflict")
    if outcome not in OUTCOMES:
        raise ReviewError("outcome must be approve, reject or request_changes")
    if outcome != "approve" and not reason.strip():
        raise ReviewError("a reason is required to reject or request changes")
    pairs = review_copy_spans(session, task)
    visible = {sp.id for _, sp in pairs}
    unknown = [s for s in cited if s not in visible]
    if unknown:
        raise ReviewError("cited spans are not part of this review", "invalid", {"span_ids": unknown})

    sub = session.get(Submission, task.submission_id)
    wf = session.get(Workflow, sub.workflow_id)
    if channel not in allowed_channels(wf):
        raise ReviewError(f"this workflow does not accept decisions made through {channel}; use "
                          f"{' or '.join(allowed_channels(wf))}", "channel_not_allowed", {"allowed": allowed_channels(wf)})
    summary = telemetry.summarise(session, task)
    summary["channel"] = channel
    warnings: list[str] = []

    if outcome == "approve":
        acked = {a.risk_flag_id for a in session.scalars(select(FlagAcknowledgement).where(
            FlagAcknowledgement.review_task_id == task.id, FlagAcknowledgement.acknowledged_at.is_not(None))).all()}
        pending = [f.id for f in session.scalars(select(RiskFlag).where(
            RiskFlag.submission_id == sub.id, RiskFlag.mandatory.is_(True))).all() if f.span_id in visible and f.id not in acked]
        if pending:
            raise ReviewError("high-risk flags must be opened and acknowledged before approving", "mandatory_flags",
                              {"flag_ids": pending})
        action, reasons = telemetry.check_policy(wf.telemetry_policy, summary)
        if action == "block":
            raise ReviewError("approval is below the minimum review standard", "below_review_standard", {"reasons": reasons})
        if action == "warn" and not confirm_warnings:
            raise ReviewError("approval is below the minimum review standard; confirm to continue", "confirm_required",
                              {"reasons": reasons})
        warnings = reasons

    # Canary scoring (deterministic) and the canary-only catch path.
    scored = canary.score(session, task, outcome, cited)
    canary_spans = {c.span_id for c in scored}
    commented = set(session.scalars(select(Comment.span_id).where(Comment.review_task_id == task.id, Comment.span_id.is_not(None))).all())
    genuine_refs = (set(cited) | commented) - canary_spans
    if any(c.caught for c in scored) and outcome != "approve" and not genuine_refs:
        # The reviewer only objected to the canary. Their objection is about review-only content, so the
        # genuine document still needs a decision: reveal the canary and ask them to decide again.
        audit.record(session, task.tenant_id, f"user:{user.id}", "decision.canary_only", "review_task", task.id,
                     {"outcome": outcome})
        return {
            "status": "canary_caught",
            "message": "You caught a canary. It has been removed: please make your decision on the genuine content.",
            "canary": [canary.reveal(session, c) for c in scored],
        }

    rate, _ = canary.catch_rate(session, task.tenant_id, user.id)
    summary["reliability"] = round(rate, 4)
    summary["load_position"] = round(assignment.load_position(session, user.id, wf, task.assigned_at), 4)
    decision = Decision(
        tenant_id=task.tenant_id, review_task_id=task.id, submission_id=sub.id, outcome=outcome, reason=reason.strip(),
        cited_span_ids=[s for s in cited if s not in canary_spans], telemetry_summary=summary, warnings=warnings,
        decided_at=utcnow(), channel=channel,
    )
    session.add(decision)
    task.status = "completed"
    task.completed_at = utcnow()
    session.flush()
    audit.record(session, task.tenant_id, f"user:{user.id}", "decision.recorded", "review_task", task.id,
                 {"submission_id": sub.id, "decision_id": decision.id, "outcome": outcome, "kind": task.kind,
                  "channel": channel, "warnings": len(warnings), "active_seconds": summary["active_seconds"], "coverage": summary["coverage"]})
    workflow.on_decision(session, task, decision)
    return {
        "status": "recorded",
        "decision": {"id": decision.id, "outcome": outcome, "warnings": warnings, "channel": channel},
        "canary": [canary.reveal(session, c) for c in scored],
        "telemetry": summary,
    }


__all__ = ["Document", "ReviewError", "queue", "review_copy_spans", "submit_decision", "task_view"]
