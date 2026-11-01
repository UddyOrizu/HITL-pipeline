"""Auditor API (read-only): evidence explorer, audit log and chain verification."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from api.deps import UserPrincipal, get_db, require_roles
from db.models import AuditEvent, Submission, Workflow
from services import assurance, audit, evidence
from services.storage import get_storage

router = APIRouter(prefix="/v1/audit", tags=["auditor"])

auditor = require_roles("auditor", "oversight_lead")


@router.get("/submissions")
def list_submissions(
    q: str | None = None, status: str | None = None, limit: int = Query(100, le=500),
    p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db),
):
    stmt = select(Submission).where(Submission.tenant_id == p.tenant_id)
    if status:
        stmt = stmt.where(Submission.status == status)
    if q:
        stmt = stmt.where(or_(Submission.id.startswith(q), Submission.external_ref.ilike(f"%{q}%")))
    out = []
    for s in db.scalars(stmt.order_by(Submission.created_at.desc()).limit(limit)).all():
        wf = db.get(Workflow, s.workflow_id)
        score = assurance.latest(db, s.id)
        out.append({"id": s.id, "external_ref": s.external_ref, "status": s.status, "workflow": wf.name if wf else None,
                    "risk_tier": s.risk_tier, "content_origin": s.content_origin, "title": (s.meta or {}).get("title"),
                    "created_at": s.created_at.isoformat() + "Z",
                    "released_at": s.released_at.isoformat() + "Z" if s.released_at else None,
                    "score": score.score if score else None, "below_threshold": score.below_threshold if score else None,
                    "has_pack": evidence.latest_pack(db, s) is not None})
    return out


def _sub(db: Session, p: UserPrincipal, submission_id: str) -> Submission:
    s = db.get(Submission, submission_id)
    if s is None or s.tenant_id != p.tenant_id:
        raise HTTPException(404, "submission not found")
    return s


@router.get("/submissions/{submission_id}/evidence")
def evidence_record(submission_id: str, p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db)):
    return evidence.build_record(db, _sub(db, p, submission_id))


@router.get("/submissions/{submission_id}/evidence.pdf")
def evidence_pdf(submission_id: str, p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db)):
    sub = _sub(db, p, submission_id)
    pack = evidence.latest_pack(db, sub)
    if pack is None:
        raise HTTPException(409, "no evidence pack yet: it is generated when the decision is released")
    return Response(get_storage().get(pack.file_uri), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="evidence-{sub.id}.pdf"'})


@router.get("/events")
def list_events(
    entity_id: str | None = None, action: str | None = None, after_seq: int = 0, limit: int = Query(200, le=1000),
    p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db),
):
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == p.tenant_id, AuditEvent.seq > after_seq)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    return [
        {"seq": e.seq, "at": e.created_at.isoformat() + "Z", "actor": e.actor, "action": e.action, "entity": e.entity,
         "entity_id": e.entity_id, "payload": e.payload, "prev_hash": e.prev_hash, "hash": e.hash}
        for e in db.scalars(stmt.order_by(AuditEvent.seq).limit(limit)).all()
    ]


@router.get("/verify")
def verify(p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db)):
    return audit.verify_chain(db, p.tenant_id)
