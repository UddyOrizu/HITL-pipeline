"""Source-application API: submit content, poll status, fetch evidence, cancel."""

from __future__ import annotations

import base64
import binascii
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi import Response as FastResponse
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from api.deps import AppPrincipal, get_db, request_channel, require_app
from api.pagination import Page, paginate
from db.models import Submission, Workflow
from services import assurance, audit, evidence, ingestion, jobs, outbound, ratelimit, webhooks, workflow
from services.config import get_settings
from services.storage import get_storage

router = APIRouter(prefix="/v1", tags=["source apps"])

RISK_TIERS = {"low", "medium", "high"}
ORIGINS = {"ai", "human", "mixed"}


class FileIn(BaseModel):
    filename: str
    content_base64: str


class SubmissionIn(BaseModel):
    workflow_id: str
    text: str | None = None
    file: FileIn | None = None
    external_ref: str | None = None
    risk_tier: str = "medium"
    ai_model: str | None = None
    content_origin: str = "ai"
    metadata: dict = Field(default_factory=dict)
    previous_submission_id: str | None = None
    reference_texts: list[str] = Field(default_factory=list)
    reference_files: list[FileIn] = Field(default_factory=list)
    idempotency_key: str | None = None


def resolve_workflow(db: Session, principal: AppPrincipal, workflow_id: str) -> Workflow:
    wf = db.scalars(
        select(Workflow).where(
            Workflow.tenant_id == principal.tenant_id,
            or_(Workflow.family_id == workflow_id, Workflow.id == workflow_id),
            Workflow.is_current.is_(True),
        )
    ).first()
    if wf is None:
        # A version id that is no longer current resolves to its family's current version.
        old = db.scalars(select(Workflow).where(Workflow.tenant_id == principal.tenant_id, Workflow.id == workflow_id)).first()
        if old:
            wf = db.scalars(select(Workflow).where(Workflow.family_id == old.family_id, Workflow.is_current.is_(True))).first()
    if wf is None:
        raise HTTPException(404, "workflow not found")
    if wf.source_app_ids and principal.app.id not in wf.source_app_ids:
        raise HTTPException(403, "workflow is not available to this source app")
    return wf


def _decode(f: FileIn) -> tuple[str, bytes]:
    try:
        return f.filename, base64.b64decode(f.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, f"file '{f.filename}' is not valid base64") from None


async def parse(request: Request) -> tuple[SubmissionIn, tuple[str, bytes] | None, list[tuple[str, bytes]]]:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        meta = form.get("metadata") or "{}"
        try:
            metadata = json.loads(meta) if isinstance(meta, str) else {}
        except json.JSONDecodeError:
            raise HTTPException(422, "metadata must be a JSON object") from None
        fields = {k: v for k, v in form.items() if isinstance(v, str) and k not in ("metadata", "reference_texts")}
        try:
            body = SubmissionIn(**fields, metadata=metadata, reference_texts=[v for v in form.getlist("reference_texts") if isinstance(v, str)])
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, str(exc)) from None
        upload = form.get("file")
        primary = (upload.filename or "upload", await upload.read()) if isinstance(upload, UploadFile) else None
        refs = [(u.filename or "reference", await u.read()) for u in form.getlist("reference_files") if isinstance(u, UploadFile)]
        return body, primary, refs
    try:
        body = SubmissionIn.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"invalid JSON body: {exc}") from None
    primary = _decode(body.file) if body.file else None
    refs = [_decode(f) for f in body.reference_files]
    return body, primary, refs


def create(db: Session, principal: AppPrincipal, body: SubmissionIn, primary: tuple[str, bytes] | None,
           refs: list[tuple[str, bytes]], idem: str | None, actor: str, channel: str) -> tuple[Submission, bool]:
    """Shared by the source-app API (and MCP, which calls it) and the web portal. Returns (submission, replayed)."""
    if idem:
        existing = db.scalars(select(Submission).where(
            Submission.tenant_id == principal.tenant_id, Submission.source_app_id == principal.app.id,
            Submission.idempotency_key == idem)).first()
        if existing:
            return existing, True
    if (primary is None) == (body.text is None):
        raise HTTPException(422, "provide exactly one of 'text' or 'file'")
    if body.risk_tier not in RISK_TIERS:
        raise HTTPException(422, f"risk_tier must be one of {sorted(RISK_TIERS)}")
    if body.content_origin not in ORIGINS:
        raise HTTPException(422, f"content_origin must be one of {sorted(ORIGINS)}")
    if body.previous_submission_id:
        prev = db.get(Submission, body.previous_submission_id)
        if prev is None or prev.tenant_id != principal.tenant_id or prev.source_app_id != principal.app.id:
            raise HTTPException(404, "previous submission not found")
    wf = resolve_workflow(db, principal, body.workflow_id)

    sub = Submission(
        tenant_id=principal.tenant_id, workflow_id=wf.id, workflow_version=wf.version, source_app_id=principal.app.id,
        external_ref=body.external_ref, idempotency_key=idem, content_origin=body.content_origin, risk_tier=body.risk_tier,
        ai_model=body.ai_model, status="received", meta=body.metadata, previous_submission_id=body.previous_submission_id,
    )
    db.add(sub)
    db.flush()
    try:
        if primary:
            ingestion.store_file(db, sub, primary[0], primary[1], "primary")
        else:
            ingestion.store_text(db, sub, body.text or "", "primary")
        for name, data in refs:
            ingestion.store_file(db, sub, name, data, "reference")
        for text in body.reference_texts:
            ingestion.store_text(db, sub, text, "reference")
    except ingestion.UnsupportedType as exc:
        db.rollback()
        raise HTTPException(415, str(exc)) from None
    except ingestion.ScanUnavailable as exc:
        db.rollback()
        raise HTTPException(503, f"upload refused: {exc}") from None
    except (ingestion.IngestionError, ingestion.VirusFound) as exc:
        db.rollback()
        raise HTTPException(422, f"{type(exc).__name__}: {exc}") from None
    audit.record(db, sub.tenant_id, actor, "submission.received", "submission", sub.id,
                 {"workflow_id": wf.family_id, "workflow_version": wf.version, "risk_tier": sub.risk_tier,
                  "content_origin": sub.content_origin, "external_ref": sub.external_ref, "channel": channel})
    db.refresh(sub)
    webhooks.emit(db, sub, "submission.received")
    db.commit()
    jobs.enqueue("process_submission", submission_id=sub.id, tenant_id=sub.tenant_id)
    db.expire_all()
    return db.get(Submission, sub.id), False


@router.post("/submissions", status_code=201)
async def create_submission(
    request: Request,
    principal: AppPrincipal = Depends(require_app),
    db: Session = Depends(get_db),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Submit text or a file (multipart or JSON with base64) for human review."""
    ratelimit.enforce(f"submissions:{principal.app.id}", get_settings().rate_limit_submissions_per_minute)
    body, primary, refs = await parse(request)
    sub, replayed = create(db, principal, body, primary, refs, idempotency_key_header or body.idempotency_key,
                           f"app:{principal.app.id}", request_channel(request))
    if replayed:
        return JSONResponse(outbound.submission_view(db, sub), status_code=200, headers={"Idempotent-Replay": "true"})
    return outbound.submission_view(db, sub)


def _own(db: Session, principal: AppPrincipal, submission_id: str) -> Submission:
    sub = db.get(Submission, submission_id)
    if sub is None or sub.tenant_id != principal.tenant_id or sub.source_app_id != principal.app.id:
        raise HTTPException(404, "submission not found")
    return sub


@router.get("/submissions")
def list_submissions(response: FastResponse, status: str | None = None, external_ref: str | None = None,
                     page: Page = Depends(), principal: AppPrincipal = Depends(require_app), db: Session = Depends(get_db)):
    """This source app's submissions, newest first (X-Total-Count has the total)."""
    q = select(Submission).where(Submission.tenant_id == principal.tenant_id, Submission.source_app_id == principal.app.id)
    if status:
        q = q.where(Submission.status == status)
    if external_ref:
        q = q.where(Submission.external_ref == external_ref)
    return [outbound.submission_view(db, s) for s in paginate(db, q.order_by(Submission.created_at.desc()), page, response)]


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: str, principal: AppPrincipal = Depends(require_app), db: Session = Depends(get_db)):
    return outbound.submission_view(db, _own(db, principal, submission_id))


@router.get("/submissions/{submission_id}/feedback")
def get_feedback(submission_id: str, principal: AppPrincipal = Depends(require_app), db: Session = Depends(get_db)):
    """Reviewer comments for a request-changes outcome, so the source can revise and resubmit."""
    sub = _own(db, principal, submission_id)
    data = {
        "submission_id": sub.id, "status": sub.status, "outcome": sub.final_outcome,
        "reason": outbound.redact(sub.final_reason, outbound.canaries_for(db, [sub.id])),
        "comments": outbound.feedback(db, sub) if sub.final_outcome else [],
    }
    outbound.assert_no_canary_leak(db, data, [sub.id])
    return data


@router.get("/submissions/{submission_id}/evidence")
def get_evidence(
    submission_id: str,
    format: str = Query("json", pattern="^(json|pdf)$"),
    principal: AppPrincipal = Depends(require_app),
    db: Session = Depends(get_db),
):
    sub = _own(db, principal, submission_id)
    if format == "pdf":
        pack = evidence.latest_pack(db, sub)
        if pack is None:
            raise HTTPException(409, "evidence pack is generated when the decision is released")
        return Response(get_storage().get(pack.file_uri), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="evidence-{sub.id}.pdf"'})
    return evidence.build_record(db, sub)


@router.post("/submissions/{submission_id}/cancel")
def cancel_submission(submission_id: str, principal: AppPrincipal = Depends(require_app), db: Session = Depends(get_db)):
    sub = _own(db, principal, submission_id)
    try:
        workflow.cancel(db, sub, f"app:{principal.app.id}")
    except workflow.TransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    return outbound.submission_view(db, sub)


@router.get("/workflows")
def list_workflows(principal: AppPrincipal = Depends(require_app), db: Session = Depends(get_db)):
    rows = db.scalars(select(Workflow).where(Workflow.tenant_id == principal.tenant_id, Workflow.is_current.is_(True))).all()
    return [
        {"id": w.family_id, "version": w.version, "name": w.name, "stages": [s.get("name") for s in w.stages or []]}
        for w in rows if not w.source_app_ids or principal.app.id in w.source_app_ids
    ]


@router.get("/assurance/summary")
def assurance_summary(days: int = Query(30, ge=1, le=365), principal: AppPrincipal = Depends(require_app), db: Session = Depends(get_db)):
    """Aggregate metrics for dashboards in the customer's own tools (no reviewer names)."""
    d = assurance.dashboard(db, principal.tenant_id, days)
    data = {
        "window_days": days,
        "catch_rate": {k: d["catch_rate"][k] for k in ("overall", "by_workflow", "by_difficulty", "by_week")},
        "review_time": d["review_time"],
        "volume": d["volume"],
        "spot_checks": d["spot_checks"],
        "scores": d["scores"],
    }
    outbound.assert_no_canary_leak(db, data, tenant_id=principal.tenant_id)
    return data
