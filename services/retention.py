"""Data retention and erasure.

Purging a submission removes the client content: stored files (originals and references), extracted text,
claim text in risk flags, canary text, comment text and reading telemetry. It keeps what proves the review
happened: decisions and their reasons, assurance scores, the audit log and the evidence pack. Only finished
submissions (approved, rejected, changes requested, deferred, cancelled) can be purged.

Each tenant sets settings.retention_days (minimum 30; unset means keep forever). The scheduler purges finished
submissions older than that (by their release date, or last update for ones never released).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from db.models import (
    Canary,
    Comment,
    Document,
    ReviewTask,
    RiskFlag,
    Section,
    Span,
    Submission,
    TelemetryEvent,
    Tenant,
    utcnow,
)
from db.session import session_scope
from services import audit
from services.storage import get_storage

log = logging.getLogger("por.retention")

FINISHED = ("approved", "rejected", "changes_requested", "deferred", "cancelled")
MIN_RETENTION_DAYS = 30


class RetentionError(ValueError):
    pass


def purge_submission(session: Session, sub: Submission, actor: str, reason: str) -> dict:
    if sub.status not in FINISHED:
        raise RetentionError("only finished submissions can be purged")
    if sub.purged_at:
        return {"files": 0, "already_purged": True}
    storage = get_storage()
    files = 0
    doc_ids = []
    for doc in sub.documents:
        doc_ids.append(doc.id)
        if not doc.original_file_uri.startswith("purged:"):
            try:
                storage.delete(doc.original_file_uri)
                files += 1
            except Exception:  # noqa: BLE001  (already gone: still mark it purged)
                log.warning("could not delete %s", doc.original_file_uri)
            doc.original_file_uri = f"purged:{doc.id}"
    if doc_ids:
        for sp in session.scalars(select(Span).where(Span.document_id.in_(doc_ids))).all():
            sp.text = ""
        for sec in session.scalars(select(Section).where(Section.document_id.in_(doc_ids))).all():
            sec.title = None
    for f in session.scalars(select(RiskFlag).where(RiskFlag.submission_id == sub.id)).all():
        f.claim_text = None
        f.rationale = ""
    for c in session.scalars(select(Canary).where(Canary.submission_id == sub.id)).all():
        c.injected_text = ""
    task_ids = list(session.scalars(select(ReviewTask.id).where(ReviewTask.submission_id == sub.id)).all())
    if task_ids:
        for cm in session.scalars(select(Comment).where(Comment.review_task_id.in_(task_ids))).all():
            cm.body = ""
            cm.suggested_text = None
        session.execute(delete(TelemetryEvent).where(TelemetryEvent.review_task_id.in_(task_ids)))
    sub.purged_at = utcnow()
    audit.record(session, sub.tenant_id, actor, "submission.content_purged", "submission", sub.id,
                 {"reason": reason, "files_deleted": files})
    return {"files": files, "already_purged": False}


def retention_days(tenant: Tenant) -> int | None:
    days = (tenant.settings or {}).get("retention_days")
    return int(days) if days else None


def sweep(limit_per_tenant: int = 200) -> int:
    """Purge finished submissions past each tenant's retention period. Called by the scheduler."""
    purged = 0
    with session_scope() as s:
        tenants = [(t.id, retention_days(t)) for t in s.scalars(select(Tenant)).all()]
    for tenant_id, days in tenants:
        if not days:
            continue
        cutoff = utcnow() - timedelta(days=days)
        with session_scope(tenant_id) as s:
            subs = s.scalars(select(Submission).where(
                Submission.tenant_id == tenant_id, Submission.status.in_(FINISHED), Submission.purged_at.is_(None),
                or_(Submission.released_at < cutoff, Submission.released_at.is_(None) & (Submission.updated_at < cutoff)),
            ).limit(limit_per_tenant)).all()
            for sub in subs:
                purge_submission(s, sub, "system:retention", f"retention_{days}_days")
                purged += 1
    if purged:
        log.info("retention: purged %s submissions", purged)
    return purged


def document_available(doc: Document) -> bool:
    return not doc.original_file_uri.startswith("purged:")
