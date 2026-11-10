"""Append-only, hash-chained audit log.

Each tenant has its own chain. hash = sha256(prev_hash + canonical JSON of the event body),
so editing or removing any event breaks every hash after it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import func, select, text
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


def head(session: Session, tenant_id: str) -> tuple[int, str]:
    """The tenant chain's latest (seq, hash), or (0, GENESIS) for an empty chain."""
    last = session.execute(
        select(AuditEvent.seq, AuditEvent.hash).where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.seq.desc()).limit(1)
    ).first()
    return (last.seq, last.hash) if last else (0, GENESIS)


def _anchor(session: Session, tenant_id: str) -> tuple[int, str] | None:
    """The newest chain head recorded in an evidence pack (stored outside the chain)."""
    from db.models import EvidencePack

    row = session.execute(
        select(EvidencePack.audit_head_seq, EvidencePack.audit_head_hash)
        .where(EvidencePack.tenant_id == tenant_id, EvidencePack.audit_head_seq.is_not(None))
        .order_by(EvidencePack.audit_head_seq.desc()).limit(1)
    ).first()
    return (row[0], row[1]) if row else None


def verify_chain(session: Session, tenant_id: str, batch: int = 2000) -> dict:
    """Recompute every hash, reading the chain in batches. Returns {"valid", "count", "broken_at", "head"}.

    The chain's head is not otherwise anchored, so removing events from the end would leave a valid-looking
    chain. Each evidence pack records the head it was built against; if the chain no longer reaches the newest
    recorded head, or the event at that seq has a different hash, the chain is reported broken ("truncated")."""
    prev = GENESIS
    expected = 1
    last_seq = 0
    hashes_at: dict[int, str] = {}
    anchor = _anchor(session, tenant_id)
    while True:
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id, AuditEvent.seq > last_seq)
            .order_by(AuditEvent.seq).limit(batch)
        ).all()
        if not events:
            break
        for ev in events:
            if ev.seq != expected or ev.prev_hash != prev or compute_hash(prev, _body(ev)) != ev.hash:
                total = session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
                return {"valid": False, "count": total, "broken_at": ev.seq}
            if anchor and ev.seq == anchor[0]:
                hashes_at[ev.seq] = ev.hash
            prev = ev.hash
            expected += 1
        last_seq = events[-1].seq
    count = expected - 1
    if anchor and (count < anchor[0] or hashes_at.get(anchor[0]) != anchor[1]):
        return {"valid": False, "count": count, "broken_at": min(count + 1, anchor[0]), "truncated": True,
                "anchored_head_seq": anchor[0]}
    return {"valid": True, "count": count, "broken_at": None, "head": prev}


def verify_events(session: Session, tenant_id: str, events: list[AuditEvent]) -> bool:
    """Check some events without walking the whole chain: each one's own hash, and its link to the event before
    it (prev_hash equals the hash stored at seq - 1, or GENESIS for seq 1)."""
    if not events:
        return True
    prev_seqs = {e.seq - 1 for e in events if e.seq > 1}
    stored = dict(session.execute(
        select(AuditEvent.seq, AuditEvent.hash).where(AuditEvent.tenant_id == tenant_id, AuditEvent.seq.in_(prev_seqs))
    ).all()) if prev_seqs else {}
    for ev in events:
        expected_prev = GENESIS if ev.seq == 1 else stored.get(ev.seq - 1)
        if expected_prev is None or ev.prev_hash != expected_prev or compute_hash(ev.prev_hash, _body(ev)) != ev.hash:
            return False
    return True


def events_for(session: Session, tenant_id: str, entity_ids: list[str]) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_id, AuditEvent.entity_id.in_(entity_ids))
            .order_by(AuditEvent.seq)
        ).all()
    )
