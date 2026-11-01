"""Processing pipeline for one submission: extract, flag, seed, then hand to the workflow engine."""

from __future__ import annotations

import logging

from db.models import Submission
from db.session import session_scope
from services import audit, canary, ingestion, jobs, risk, webhooks, workflow

log = logging.getLogger("por.pipeline")


@jobs.job("process_submission")
def process_submission(submission_id: str, tenant_id: str = "*") -> None:
    with session_scope(tenant_id) as session:
        sub = session.get(Submission, submission_id)
        if sub is None or sub.status != "received":
            return
        workflow.transition(session, sub, "processing", "system:pipeline")
        wf = workflow.workflow_of(session, sub)
        try:
            for doc in sub.documents:
                ingestion.extract_document(session, doc)
            primary = next(d for d in sub.documents if d.role == "primary")
            sub.content_hash = ingestion.content_hash(session, primary.id)
            risk.flag_submission(session, sub, wf)
            canary.inject(session, sub, wf)
        except Exception as exc:  # noqa: BLE001
            log.exception("processing failed for %s", submission_id)
            session.rollback()
            sub = session.get(Submission, submission_id)
            sub.processing_error = str(exc)[:2000]
            workflow.transition(session, sub, "deferred", "system:pipeline", reason="processing_failed")
            audit.record(session, sub.tenant_id, "system:pipeline", "submission.processing_failed", "submission", sub.id,
                         {"error": str(exc)[:500]})
            webhooks.emit(session, sub, "submission.deferred", {"reason": "processing_failed", "error": str(exc)[:500]})
            return
        workflow.start_review(session, sub)
