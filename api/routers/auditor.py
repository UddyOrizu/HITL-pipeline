"""Auditor API (read-only): evidence explorer, audit log and chain verification."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from api.downloads import stored_file
from api.deps import UserPrincipal, get_db, require_roles
from api.pagination import Page, paginate, set_headers
from db.models import AuditEvent, Submission, User, Workflow
from services import assurance, audit, evidence
from services.config import get_settings

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
    record = evidence.build_record(db, _sub(db, p, submission_id))
    if not auditor_only(p):
        return record
    # Same reviewer pseudonyms as the audit log, so the record can't be used to unmask them.
    redactor = _Redactor(db, p.tenant_id)
    trail = [redactor.event(e) for e in record.get("audit_trail", [])]
    record = redactor._value({k: v for k, v in record.items() if k != "audit_trail"})
    record["audit_trail"] = trail
    record["redacted"] = True
    return record


@router.get("/submissions/{submission_id}/evidence.pdf")
def evidence_pdf(submission_id: str, link: bool = False, p: UserPrincipal = Depends(auditor),
                 db: Session = Depends(get_db)):
    sub = _sub(db, p, submission_id)
    pack = evidence.latest_pack(db, sub)
    if pack is None:
        raise HTTPException(409, "no evidence pack yet: it is generated when the decision is released")
    return stored_file(pack.file_uri, f"evidence-{sub.id}.pdf", "application/pdf", link)


def _utc(dt: datetime) -> datetime:
    """Naive UTC, like the database. Times with an offset are converted; naive times are taken as UTC."""
    from datetime import UTC

    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def _event_out(e: AuditEvent) -> dict:
    return {"seq": e.seq, "at": e.created_at.isoformat() + "Z", "actor": e.actor, "action": e.action, "entity": e.entity,
            "entity_id": e.entity_id, "payload": e.payload, "prev_hash": e.prev_hash, "hash": e.hash}


# --- Reviewer-level detail ----------------------------------------------------------------
# Auditors see the log without reviewer-level detail: reviewers appear under a stable pseudonym and user events
# carry no email addresses. Oversight leads and admins see the log as stored. prev_hash and hash always refer to the
# stored events, so a redacted page can't be re-hashed by the reader: GET /v1/audit/verify checks the stored chain
# and is the authoritative integrity check.


def auditor_only(p: UserPrincipal) -> bool:
    return p.has("auditor") and not p.has("oversight_lead")


def reviewer_pseudonym(tenant_id: str, user_id: str) -> str:
    digest = hmac.new(get_settings().secret_key.encode(), f"reviewer:{tenant_id}:{user_id}".encode(), hashlib.sha256)
    return "reviewer:" + digest.hexdigest()[:12]


class _Redactor:
    def __init__(self, db: Session, tenant_id: str):
        self.tenant_id = tenant_id
        users = db.scalars(select(User).where(User.tenant_id == tenant_id)).all()
        self.reviewers = {u.id: reviewer_pseudonym(tenant_id, u.id) for u in users if "reviewer" in (u.roles or [])}

    def actor_filter(self, actor: str) -> str:
        """Translate a pseudonym back for filtering; a real reviewer id is not accepted (it would reveal the pseudonym)."""
        if actor.startswith("reviewer:"):
            return next((f"user:{uid}" for uid, ps in self.reviewers.items() if ps == actor), "reviewer:unknown")
        if actor.startswith("user:") and actor[5:] in self.reviewers:
            return "reviewer:unknown"
        return actor

    def _value(self, v):
        if isinstance(v, str):
            if v in self.reviewers:
                return self.reviewers[v]
            if v.startswith("user:") and v[5:] in self.reviewers:
                return self.reviewers[v[5:]]
            return v
        if isinstance(v, list):
            return [self._value(x) for x in v]
        if isinstance(v, dict):
            return {k: self._value(x) for k, x in v.items()}
        return v

    def _drop_emails(self, v):
        if isinstance(v, dict):
            return {k: self._drop_emails(x) for k, x in v.items() if "email" not in k.lower()}
        if isinstance(v, list):
            return [self._drop_emails(x) for x in v]
        return v

    def event(self, out: dict) -> dict:
        out = dict(out)
        out["actor"] = self._value(out["actor"])
        out["entity_id"] = self._value(out["entity_id"])
        payload = self._value(out["payload"] or {})
        if out["action"].startswith("user."):
            payload = self._drop_emails(payload)
        out["payload"] = payload
        out["redacted"] = True
        return out


@router.get("/events")
def list_events(
    response: Response,
    entity_id: str | None = None, entity: str | None = None, action: str | None = None,
    actor: str | None = None, since: datetime | None = None, until: datetime | None = None,
    after_seq: int = 0, before_seq: int | None = None,
    order: str = Query("asc", pattern="^(asc|desc)$"), limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db),
):
    """The tenant's audit log. Page forward with after_seq (order=asc) or backward from the newest with before_seq
    (order=desc), or with offset. action matches a prefix ("user." for every user event). X-Total-Count has the number
    of matching events and X-Next-Offset the offset of the next page, as on other lists.

    Auditors (without oversight or admin access) get reviewer actors as stable pseudonyms (reviewer:<hash>) and user
    events without email addresses; filter by a pseudonym with actor=reviewer:<hash>. prev_hash/hash describe the
    stored events: GET /v1/audit/verify is the authoritative chain check."""
    redactor = _Redactor(db, p.tenant_id) if auditor_only(p) else None
    if redactor and actor:
        actor = redactor.actor_filter(actor)
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
    ordered = stmt.order_by(AuditEvent.seq.desc() if order == "desc" else AuditEvent.seq)
    events = db.scalars(ordered.offset(offset).limit(limit)).all()
    set_headers(response, total, Page(offset, limit), len(events))
    out = [_event_out(e) for e in events]
    return [redactor.event(o) for o in out] if redactor else out


@router.get("/actions")
def list_actions(p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db)):
    """Distinct actions in the tenant's log, for filters."""
    return sorted(db.scalars(select(AuditEvent.action).where(AuditEvent.tenant_id == p.tenant_id).distinct()).all())


@router.get("/verify")
def verify(p: UserPrincipal = Depends(auditor), db: Session = Depends(get_db)):
    return audit.verify_chain(db, p.tenant_id)
