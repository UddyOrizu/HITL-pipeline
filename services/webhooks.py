"""Integration service: signed webhook delivery with exponential backoff retries for 24 hours."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import SourceApp, Submission, WebhookDelivery, new_id, utcnow
from db.session import session_scope
from services import audit, jobs, metrics, netguard, outbound
from services.config import get_settings
from services.hooks import on_commit

log = logging.getLogger("por.webhooks")

EVENTS = {"submission.received", "submission.in_review", "submission.decided", "submission.deferred", "submission.escalated",
          "submission.cancelled"}

_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Tests inject an httpx.MockTransport here."""
    global _transport
    _transport = transport


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify(secret: str, header: str, body: bytes, tolerance_seconds: int = 300) -> bool:
    """Helper source apps can copy to verify our signature."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(time.time() - ts) > tolerance_seconds:
        return False
    return hmac.compare_digest(sign(secret, ts, body), header)


def emit(session: Session, sub: Submission, event: str, data: dict | None = None) -> WebhookDelivery | None:
    assert event in EVENTS, event
    app = session.get(SourceApp, sub.source_app_id)
    body = {
        "id": new_id(),
        "event": event,
        "created_at": utcnow().isoformat() + "Z",
        "data": {"submission": outbound.submission_view(session, sub), **(data or {})},
    }
    outbound.assert_no_canary_leak(session, body, [sub.id])
    jobs.publish(event, {"tenant_id": sub.tenant_id, "submission_id": sub.id, "status": sub.status, **(data or {})}, session=session)
    if not app or not app.webhook_url:
        return None
    delivery = WebhookDelivery(
        tenant_id=sub.tenant_id, source_app_id=app.id, submission_id=sub.id, event=event, payload=body,
        status="pending", next_attempt_at=utcnow(),
    )
    session.add(delivery)
    session.flush()
    delivery_id = delivery.id
    on_commit(session, lambda: jobs.enqueue("deliver_webhook", delivery_id=delivery_id))
    return delivery


def backoff_seconds(attempts: int) -> int:
    return min(30 * (2 ** (attempts - 1)), 3600)


def _attempt(session: Session, d: WebhookDelivery) -> None:
    app = session.get(SourceApp, d.source_app_id)
    body = json.dumps(d.payload, separators=(",", ":"), sort_keys=True).encode()
    outbound.assert_no_canary_leak(session, d.payload, [d.submission_id] if d.submission_id else None, tenant_id=d.tenant_id)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ProofOfReview-Webhooks/1.0",
        "X-PoR-Event": d.event,
        "X-PoR-Delivery": d.id,
        "X-PoR-Signature": sign(app.webhook_secret, int(time.time()), body),
    }
    d.attempts += 1
    blocked = False
    try:
        if not app.webhook_url:
            raise netguard.UnsafeTarget("the source app no longer has a webhook URL")
        netguard.check_url(app.webhook_url, resolve=_transport is None)
        with httpx.Client(transport=_transport, timeout=get_settings().webhook_timeout_seconds,
                          follow_redirects=False) as client:
            resp = client.post(app.webhook_url, content=body, headers=headers)
        d.last_status_code = resp.status_code
        ok = 200 <= resp.status_code < 300
        d.last_error = None if ok else resp.text[:500]
    except netguard.UnsafeTarget as exc:
        ok, blocked = False, True
        d.last_status_code = None
        d.last_error = f"blocked: {exc}"[:500]
    except httpx.HTTPError as exc:
        ok = False
        d.last_status_code = None
        d.last_error = str(exc)[:500]
    metrics.inc("por_webhook_attempts_total", {"outcome": "delivered" if ok else ("blocked" if blocked else "failed")})
    now = utcnow()
    if ok:
        d.status = "delivered"
        d.delivered_at = now
    elif blocked:
        d.status = "failed"
    else:
        deadline = d.created_at + timedelta(hours=get_settings().webhook_retry_window_hours)
        nxt = now + timedelta(seconds=backoff_seconds(d.attempts))
        if nxt > deadline:
            d.status = "failed"
        else:
            d.next_attempt_at = nxt
    audit.record(
        session, d.tenant_id, "system:webhooks", f"webhook.{d.status if d.status != 'pending' else 'retry_scheduled'}",
        "webhook_delivery", d.id, {"event": d.event, "attempts": d.attempts, "status_code": d.last_status_code},
    )


@jobs.job("deliver_webhook")
def deliver(delivery_id: str) -> None:
    with session_scope() as session:
        d = session.get(WebhookDelivery, delivery_id)
        if d and d.status == "pending":
            _attempt(session, d)


def retry_due(limit: int = 100) -> int:
    """Called periodically by the worker."""
    with session_scope() as session:
        due = session.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= utcnow())
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(limit)
        ).all()
        for d in due:
            _attempt(session, d)
        return len(due)


def redeliver(session: Session, d: WebhookDelivery) -> None:
    d.status = "pending"
    d.next_attempt_at = utcnow()
    _attempt(session, d)
