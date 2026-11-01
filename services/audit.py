"""Append-only, hash-chained audit log.

Each tenant has its own chain. hash = sha256(prev_hash + canonical JSON of the event body),
so editing or removing any event breaks every hash after it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.models import AuditEvent, new_id, utcnow
from db.session import is_postgres

GENESIS = "0" * 64


def _canonical(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, body: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(body)).encode()).hexdigest()


def _body(ev: AuditEvent) -> dict:
    created = ev.created_at.isoformat() if isinstance(ev.created_at, datetime) else str(ev.created_at)
    return {
        "id": ev.id,
        "tenant_id": ev.tenant_id,
        "seq": ev.seq,
        "actor": ev.actor,
        "action": ev.action,
        "entity": ev.entity,
        "entity_id": ev.entity_id,
        "payload": ev.payload,
        "created_at": created,
    }


def record(
    session: Session,
    tenant_id: str,
    actor: str,
    action: str,
    entity: str,
    entity_id: str | None = None,
    payload: dict | None = None,
) -> AuditEvent:
    """Append one event to the tenant's chain. Call this on every state change."""
    if is_postgres(session):
        # Serialise chain writes per tenant inside the current transaction.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:t))"), {"t": tenant_id})
    session.flush()
    last = session.execute(
        select(AuditEvent.seq, AuditEvent.hash)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.seq.desc())
        .limit(1)
    ).first()
    seq = (last.seq + 1) if last else 1
    prev_hash = last.hash if last else GENESIS
    ev = AuditEvent(
        id=new_id(),
        tenant_id=tenant_id,
        seq=seq,
        actor=actor,
        action=action,
        entity=entity,
        entity_id=entity_id,
        payload=json.loads(_canonical(payload or {})),
        prev_hash=prev_hash,
        created_at=utcnow().replace(microsecond=0),
    )
    ev.hash = compute_hash(prev_hash, _body(ev))
    session.add(ev)
    session.flush()
    return ev


def verify_chain(session: Session, tenant_id: str) -> dict:
    """Recompute every hash. Returns {"valid": bool, "count": n, "broken_at": seq | None}."""
    events = session.scalars(
        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq)
    ).all()
    prev = GENESIS
    for i, ev in enumerate(events, start=1):
        if ev.seq != i or ev.prev_hash != prev or compute_hash(prev, _body(ev)) != ev.hash:
            return {"valid": False, "count": len(events), "broken_at": ev.seq}
        prev = ev.hash
    return {"valid": True, "count": len(events), "broken_at": None, "head": prev}


def events_for(session: Session, tenant_id: str, entity_ids: list[str]) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_id, AuditEvent.entity_id.in_(entity_ids))
            .order_by(AuditEvent.seq)
        ).all()
    )
