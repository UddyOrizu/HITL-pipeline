"""Auditor API (read-only): evidence explorer, audit log and chain verification."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import Response as RawResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from api.deps import UserPrincipal, get_db, require_roles
from api.pagination import Page, paginate
from db.models import AuditEvent, Submission, Workflow
from services import assurance, audit, evidence
from services.storage import get_storage

router = APIRouter(prefix="/v1/audit", tags=["auditor"])

auditor = require_roles("auditor", "oversight_lead")


@router.get("/submissions")
def list_submissions(
    response: Response, q: str | None = None, status: str | None = None, page: Page = Depends(),
    p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db),
):
    stmt = select(Submission).where(Submission.tenant_id == p.tenant_id)
    if status:
        stmt = stmt.where(Submission.status == status)
    if q:
        stmt = stmt.where(or_(Submission.id.startswith(q), Submission.external_ref.ilike(f"%{q}%"),
                              Submission.meta["title"].as_string().ilike(f"%{q}%")))
    out = []
    for s in paginate(db, stmt.order_by(Submission.created_at.desc()), page, response):
        wf = db.get(Workflow, s.workflow_id)
        score = assurance.latest(db, s.id)
        out.append({"id": s.id, "external_ref": s.external_ref, "status": s.status, "workflow": wf.name if wf else None,
                    "risk_tier": s.risk_tier, "content_origin": s.content_origin, "title": (s.meta or {}).get("title"),
                    "created_at": s.created_at.isoformat() + "Z",
                    "released_at": s.released_at.isoformat() + "Z" if s.released_at else None,
                    "score": score.score if score else None, "below_threshold": score.below_threshold if score else None,
                    "has_pack": evidence.latest_pack(db, s) is not None,
                    "purged": bool(s.purged_at)})
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
    return RawResponse(get_storage().get(pack.file_uri), media_type="application/pdf",
                       headers={"Content-Disposition": f'attachment; filename="evidence-{sub.id}.pdf"'})


def _utc(dt: datetime) -> datetime:
    """Naive UTC, like the database. Times with an offset are converted; naive times are taken as UTC."""
    from datetime import UTC

    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def _event_out(e: AuditEvent) -> dict:
    return {"seq": e.seq, "at": e.created_at.isoformat() + "Z", "actor": e.actor, "action": e.action, "entity": e.entity,
            "entity_id": e.entity_id, "payload": e.payload, "prev_hash": e.prev_hash, "hash": e.hash}


@router.get("/events")
def list_events(
    response: Response,
    entity_id: str | None = None, entity: str | None = None, action: str | None = None,
    actor: str | None = None, since: datetime | None = None, until: datetime | None = None,
    after_seq: int = 0, before_seq: int | None = None,
    order: str = Query("asc", pattern="^(asc|desc)$"), limit: int = Query(200, ge=1, le=1000),
    p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db),
):
    """The tenant's audit log. Page forward with after_seq (order=asc) or backward from the newest with before_seq
    (order=desc). action matches a prefix ("user." for every user event)."""
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == p.tenant_id, AuditEvent.seq > after_seq)
    if before_seq is not None:
        stmt = stmt.where(AuditEvent.seq < before_seq)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    if entity:
        stmt = stmt.where(AuditEvent.entity == entity)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action) if action.endswith(".") else AuditEvent.action == action)
    if actor:
        stmt = stmt.where(AuditEvent.actor == actor)
    if since:
        stmt = stmt.where(AuditEvent.created_at >= _utc(since))
    if until:
        stmt = stmt.where(AuditEvent.created_at < _utc(until))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    response.headers["X-Total-Count"] = str(total)
    ordered = stmt.order_by(AuditEvent.seq.desc() if order == "desc" else AuditEvent.seq)
    return [_event_out(e) for e in db.scalars(ordered.limit(limit)).all()]


@router.get("/actions")
def list_actions(p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db)):
    """Distinct actions in the tenant's log, for filters."""
    return sorted(db.scalars(select(AuditEvent.action).where(AuditEvent.tenant_id == p.tenant_id).distinct()).all())


@router.get("/verify")
def verify(p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db)):
    return audit.verify_chain(db, p.tenant_id)
