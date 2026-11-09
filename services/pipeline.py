"""Processing pipeline for one submission: extract, flag, seed, then hand to the workflow engine.

Transient failures (document conversion service or model provider unavailable, network errors) are retried
with backoff up to POR_PROCESSING_MAX_ATTEMPTS. Permanent failures (a file that cannot be read) defer the
submission straight away. Deferred items can be retried by an oversight lead.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx

from db.models import Submission, utcnow
from db.session import session_scope
from services import audit, canary, ingestion, jobs, model_gateway, risk, webhooks, workflow
from services.config import get_settings

log = logging.getLogger("por.pipeline")

TRANSIENT = (ingestion.ConversionUnavailable, httpx.HTTPError, ConnectionError, TimeoutError, OSError)


def is_transient(exc: Exception) -> bool:
    return isinstance(exc, TRANSIENT) and not isinstance(exc, ingestion.ExtractionError | ingestion.UnsupportedType)


def retry_delay(attempt: int) -> timedelta:
    return timedelta(seconds=min(60 * (2 ** (attempt - 1)), 1800))


@jobs.job("process_submission")
def process_submission(submission_id: str, tenant_id: str = "*") -> None:
    with session_scope(tenant_id) as session:
        sub = session.get(Submission, submission_id, with_for_update=True)
        if sub is None or sub.status != "received":
            return
        sub.processing_attempts = (sub.processing_attempts or 0) + 1
        sub.retry_at = None
        workflow.transition(session, sub, "processing", "system:pipeline")
        wf = workflow.workflow_of(session, sub)
        calls: list = []
        try:
            with model_gateway.call_context(sub.tenant_id, sub.id) as calls:
                for doc in sub.documents:
                    ingestion.extract_document(session, doc)
                primary = next(d for d in sub.documents if d.role == "primary")
                sub.content_hash = ingestion.content_hash(session, primary.id)
                risk.flag_submission(session, sub, wf)
                canary.inject(session, sub, wf)
        except Exception as exc:  # noqa: BLE001
            log.exception("processing failed for %s", submission_id)
            session.rollback()
            model_gateway.persist(session, calls)
            _failed(session, submission_id, exc)
            return
        model_gateway.persist(session, calls)
        sub.processing_error = None
        workflow.start_review(session, sub)


def _failed(session, submission_id: str, exc: Exception) -> None:
    sub = session.get(Submission, submission_id)
    attempts = (sub.processing_attempts or 0) + 1  # the rollback undid this attempt's increment
    sub.processing_attempts = attempts
    sub.processing_error = f"{type(exc).__name__}: {exc}"[:2000]
    if is_transient(exc) and attempts < get_settings().processing_max_attempts:
        sub.retry_at = utcnow() + retry_delay(attempts)
        audit.record(session, sub.tenant_id, "system:pipeline", "submission.processing_retry_scheduled", "submission",
                     sub.id, {"error": str(exc)[:500], "attempt": attempts, "retry_at": sub.retry_at.isoformat()})
        return  # status stays "received"; the scheduler re-queues it when retry_at passes
    workflow.transition(session, sub, "deferred", "system:pipeline", reason="processing_failed")
    audit.record(session, sub.tenant_id, "system:pipeline", "submission.processing_failed", "submission", sub.id,
                 {"error": str(exc)[:500], "attempts": attempts})
    webhooks.emit(session, sub, "submission.deferred", {"reason": "processing_failed", "error": str(exc)[:500]})
