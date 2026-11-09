"""Everything that leaves the platform for a source application is built here.

Hard rule: canary text never appears in any outbound payload. Payloads are built without canary data,
free text written by reviewers is redacted if it quotes a canary, and every payload is checked by
assert_no_canary_leak before it is returned or sent.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Canary, Comment, Decision, ReviewTask, Span, Submission, Workflow
from services import assurance

REDACTED = "[redacted: refers to review-only content]"


class CanaryLeakError(RuntimeError):
    pass


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _fragments(canary: Canary) -> list[str]:
    frags = [_norm(canary.injected_text)]
    if canary.type != "factual_error":
        # Inserted canaries are wholly invented, so any long sentence from them is a leak too.
        frags += [_norm(s) for s in re.split(r"(?<=[.!?])\s+", canary.injected_text) if len(s) >= 30]
    return [f for f in frags if f]


def canaries_for(session: Session, submission_ids: list[str]) -> list[Canary]:
    if not submission_ids:
        return []
    return list(session.scalars(select(Canary).where(Canary.submission_id.in_(submission_ids))).all())


def redact(text: str | None, canaries: list[Canary]) -> str | None:
    if not text:
        return text
    norm = _norm(text)
    for c in canaries:
        if any(f in norm for f in _fragments(c)):
            return REDACTED
    return text


def assert_no_canary_leak(session: Session, payload, submission_ids: list[str] | None = None, tenant_id: str | None = None) -> None:
    """Raise CanaryLeakError if any canary text appears anywhere in the payload."""
    if submission_ids is None:
        if tenant_id is None:
            raise ValueError("submission_ids or tenant_id required")
        canaries = list(session.scalars(select(Canary).where(Canary.tenant_id == tenant_id)).all())
    else:
        canaries = canaries_for(session, submission_ids)
    if not canaries:
        return
    blob = _norm(json.dumps(payload, default=str))
    for c in canaries:
        for f in _fragments(c):
            if f in blob:
                raise CanaryLeakError(f"canary {c.id} text found in outbound payload")


def _canary_span_ids(canaries: list[Canary]) -> set[str]:
    return {c.span_id for c in canaries}


def feedback(session: Session, sub: Submission) -> list[dict]:
    """Reviewer comments for the source app, excluding anything on canary spans."""
    canaries = canaries_for(session, [sub.id])
    hidden = _canary_span_ids(canaries)
    rows = session.execute(
        select(Comment, ReviewTask)
        .join(ReviewTask, ReviewTask.id == Comment.review_task_id)
        .where(ReviewTask.submission_id == sub.id, ReviewTask.kind == "primary", ReviewTask.status == "completed")
        .order_by(Comment.created_at)
    ).all()
    out = []
    for c, t in rows:
        if c.span_id in hidden:
            continue
        span = session.get(Span, c.span_id) if c.span_id else None
        out.append({
            "span_id": c.span_id,
            # The passage the comment is about (original text only: canary spans are skipped above).
            "quote": span.text[:400] if span is not None and not span.review_only else None,
            "kind": c.kind,
            "body": redact(c.body, canaries),
            "suggested_text": redact(c.suggested_text, canaries),
            "stage": t.stage_name,
            "created_at": c.created_at.isoformat() + "Z",
        })
    return out


def submission_view(session: Session, sub: Submission) -> dict:
    """Status as seen by a source application."""
    wf = session.get(Workflow, sub.workflow_id)
    stages = wf.stages if wf else []
    data = {
        "id": sub.id,
        "external_ref": sub.external_ref,
        "workflow_id": wf.family_id if wf else sub.workflow_id,
        "workflow_version": sub.workflow_version,
        "status": sub.status,
        "current_stage": stages[sub.stage_index]["name"] if sub.status == "in_review" and sub.stage_index < len(stages) else None,
        "content_origin": sub.content_origin,
        "risk_tier": sub.risk_tier,
        "ai_model": sub.ai_model,
        "metadata": sub.meta,
        "previous_submission_id": sub.previous_submission_id,
        "escalated": sub.escalated,
        "created_at": sub.created_at.isoformat() + "Z",
        "decision": decision_view(session, sub) if sub.final_outcome else None,
    }
    assert_no_canary_leak(session, data, [sub.id])
    return data


def decision_view(session: Session, sub: Submission) -> dict:
    from services.config import get_settings

    canaries = canaries_for(session, [sub.id])
    reviewer_ids = list(
        session.scalars(
            select(ReviewTask.reviewer_id)
            .join(Decision, Decision.review_task_id == ReviewTask.id)
            .where(ReviewTask.submission_id == sub.id)
            .order_by(Decision.decided_at)
        ).all()
    )
    data = {
        "outcome": sub.final_outcome,
        "reason": redact(sub.final_reason, canaries),
        "reviewer_ids": reviewer_ids,
        "decided_at": sub.released_at.isoformat() + "Z" if sub.released_at else None,
        "assurance": assurance.score_payload(assurance.latest(session, sub.id)),
        "evidence_url": f"{get_settings().public_base_url}/v1/submissions/{sub.id}/evidence",
        "comments": feedback(session, sub) if sub.final_outcome == "changes_requested" else [],
    }
    assert_no_canary_leak(session, data, [sub.id])
    return data
