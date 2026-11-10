"""Web portal: staff with the submitter role send work for review from the browser.

Uploads go through exactly the same path as the source-app API, under a built-in "Web portal" source app
(one per tenant, no usable API key). Each submission records who sent it, and the submitter's email is the
author for the conflict rule, so nobody is assigned to review their own work. Submitters see what a source
app sees: status, decision, feedback and the evidence pack. Never canaries, telemetry or reviewer names.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.downloads import stored_file
from api.deps import AppPrincipal, UserPrincipal, get_db, request_channel, require_roles
from api.pagination import Page, paginate
from api.routers.submissions import create, parse
from db.models import SourceApp, Submission, Workflow
from services import evidence, outbound, ratelimit, workflow
from services.config import get_settings
from services.security import generate_api_key, generate_secret

router = APIRouter(prefix="/v1/portal", tags=["web portal"])

submitter = require_roles("submitter")
PORTAL_NAME = "Web portal"


def portal_app(db: Session, tenant_id: str) -> SourceApp:
    app = db.scalars(select(SourceApp).where(SourceApp.tenant_id == tenant_id, SourceApp.kind == "portal")).first()
    if app is None:
        _, prefix, key_hash = generate_api_key()  # the key itself is discarded: the portal app is never called directly
        app = SourceApp(tenant_id=tenant_id, name=PORTAL_NAME, kind="portal", api_key_prefix=prefix, api_key_hash=key_hash,
                        webhook_url=None, webhook_secret=generate_secret())
        db.add(app)
        db.flush()
    return app


def scoped_key(user_id: str, key: str | None) -> str | None:
    """Idempotency keys are unique per source app, and every submitter shares the portal app, so a person's key is
    stored under their user id: someone else reusing the same key gets their own submission, never this one."""
    if not key:
        return None
    if len(key) > 120:
        key = "sha256:" + hashlib.sha256(key.encode()).hexdigest()
    return f"user:{user_id}:{key}"


def _mine(db: Session, p: UserPrincipal, submission_id: str) -> Submission:
    sub = db.get(Submission, submission_id)
    app = portal_app(db, p.tenant_id)
    if (sub is None or sub.tenant_id != p.tenant_id or sub.source_app_id != app.id
            or (sub.meta or {}).get("submitted_by") != p.user.id):
        raise HTTPException(404, "submission not found")
    return sub


def _view(db: Session, sub: Submission) -> dict:
    data = outbound.submission_view(db, sub)
    data["title"] = (sub.meta or {}).get("title") or next((d.filename for d in sub.documents if d.role == "primary"), None)
    data["has_evidence_pack"] = evidence.latest_pack(db, sub) is not None
    wf = db.get(Workflow, sub.workflow_id)
    data["workflow_id"] = wf.family_id if wf else None
    data["previous_submission_id"] = sub.previous_submission_id
    data["feedback"] = outbound.feedback(db, sub) if sub.final_outcome else []
    outbound.assert_no_canary_leak(db, data, [sub.id])
    return data


@router.get("/workflows")
def list_workflows(p: UserPrincipal = Depends(submitter), db: Session = Depends(get_db)):
    app = portal_app(db, p.tenant_id)
    rows = db.scalars(select(Workflow).where(Workflow.tenant_id == p.tenant_id, Workflow.is_current.is_(True))
                      .order_by(Workflow.name)).all()
    return [{"id": w.family_id, "name": w.name, "stages": [s.get("name") for s in w.stages or []]}
            for w in rows if not w.source_app_ids or app.id in w.source_app_ids]


@router.post("/submissions", status_code=201)
async def submit(request: Request, p: UserPrincipal = Depends(submitter), db: Session = Depends(get_db)):
    """Multipart form (file or text) or JSON, same fields as POST /v1/submissions. Rate limited per person
    (POR_RATE_LIMIT_SUBMISSIONS_PER_MINUTE); an idempotency_key replays only your own earlier submission."""
    ratelimit.enforce(f"portal_submissions:{p.user.id}", get_settings().rate_limit_submissions_per_minute)
    body, primary, refs = await parse(request)
    app = portal_app(db, p.tenant_id)
    if body.previous_submission_id:
        _mine(db, p, body.previous_submission_id)  # a revision must follow one of your own submissions
    body.metadata = {**(body.metadata or {}), "submitted_by": p.user.id, "author_email": p.user.email,
                     "submitted_via": "portal"}
    key = request.headers.get("idempotency-key") or body.idempotency_key
    sub, _ = create(db, AppPrincipal(app), body, primary, refs, scoped_key(p.user.id, key), f"user:{p.user.id}",
                    request_channel(request))
    return _view(db, sub)


@router.get("/submissions")
def list_mine(response: Response, status: str | None = Query(None), page: Page = Depends(),
              p: UserPrincipal = Depends(submitter), db: Session = Depends(get_db)):
    app = portal_app(db, p.tenant_id)
    q = select(Submission).where(Submission.tenant_id == p.tenant_id, Submission.source_app_id == app.id,
                                 Submission.meta["submitted_by"].as_string() == p.user.id)
    if status:
        q = q.where(Submission.status == status)
    return [_view(db, s) for s in paginate(db, q.order_by(Submission.created_at.desc()), page, response)]


@router.get("/submissions/{submission_id}")
def get_mine(submission_id: str, p: UserPrincipal = Depends(submitter), db: Session = Depends(get_db)):
    return _view(db, _mine(db, p, submission_id))


@router.get("/submissions/{submission_id}/evidence.pdf")
def evidence_pdf(submission_id: str, link: bool = False, p: UserPrincipal = Depends(submitter),
                 db: Session = Depends(get_db)):
    sub = _mine(db, p, submission_id)
    pack = evidence.latest_pack(db, sub)
    if pack is None:
        raise HTTPException(409, "the evidence pack is generated when the decision is released")
    return stored_file(pack.file_uri, f"evidence-{sub.id}.pdf", "application/pdf", link)


@router.post("/submissions/{submission_id}/cancel")
def cancel(submission_id: str, p: UserPrincipal = Depends(submitter), db: Session = Depends(get_db)):
    sub = _mine(db, p, submission_id)
    try:
        workflow.cancel(db, sub, f"user:{p.user.id}")
    except workflow.TransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    return _view(db, sub)
