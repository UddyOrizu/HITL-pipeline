"""Job runner and event bus.

Inline mode (dev/test, or no Redis) runs jobs and event handlers immediately in-process. With Redis jobs on,
jobs and events go onto Redis Streams and are handled only by `python -m services.worker`, so each handler runs
exactly once per event however many API replicas there are. Failed jobs and handlers are retried with backoff
(POR_JOB_MAX_ATTEMPTS) and then moved to the dead-letter stream (inspect and replay it with
`python -m services.worker dead list|replay <id>|purge`).

Events published with a session use a transactional outbox when Redis jobs are on: the event is written to the
event_outbox table in the same transaction as the change, copied to Redis right after the commit, and marked
published. If the process dies in between, or Redis is unreachable, the `relay_outbox` periodic task copies it
later. Delivery is therefore at least once: handlers must tolerate seeing an event twice. Events published without
a session go straight to Redis (retried with backoff, then run in-process as a last resort, logged as an error).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from services.config import get_settings

log = logging.getLogger("por.jobs")

JOB_STREAM = "por:jobs"
EVENT_STREAM = "por:events"
DEAD_STREAM = "por:dead"
DELAYED = "por:delayed"  # sorted set: message JSON scored by the time it becomes due

_registry: dict[str, Callable[..., None]] = {}
_subscribers: dict[str, list[Callable[[dict], None]]] = {}


def job(name: str):
    def deco(fn: Callable[..., None]):
        _registry[name] = fn
        return fn

    return deco


_clients: dict[str, object] = {}


def _redis():
    """One client (and connection pool) per Redis URL, shared by the process."""
    import redis

    url = get_settings().redis_url
    client = _clients.get(url)
    if client is None:
        client = _clients[url] = redis.Redis.from_url(url, socket_connect_timeout=5, health_check_interval=30)
    return client


PUBLISH_DELAYS = (0.2, 0.5, 1.0)  # seconds between attempts when Redis is unreachable


def _xadd_with_retry(what: str, stream: str, fields: dict, **kwargs) -> None:
    """XADD, retried with backoff on Redis errors. Raises the last error."""
    for attempt, delay in enumerate((0.0, *PUBLISH_DELAYS)):
        if delay:
            time.sleep(delay)
        try:
            _redis().xadd(stream, fields, **kwargs)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("redis unavailable publishing %s (attempt %s): %s", what, attempt + 1, exc)
            last = exc
    from services import metrics

    metrics.inc("por_publish_failures_total", {"stream": stream})
    raise last


def uses_redis() -> bool:
    s = get_settings()
    return bool(s.redis_url) and not s.inline_jobs


def enqueue(name: str, **kwargs) -> None:
    if not uses_redis():
        run(name, kwargs)
        return
    try:
        _xadd_with_retry(f"job {name}", JOB_STREAM, {"name": name, "kwargs": json.dumps(kwargs, default=str), "attempt": "1"})
    except Exception:
        # Submissions whose processing job never arrives are re-queued by the scheduler (recover_submissions).
        log.error("JOB NOT QUEUED: %s %s (Redis unreachable)", name, json.dumps(kwargs, default=str)[:500])
        raise


def run(name: str, kwargs: dict) -> None:
    _load_jobs()
    fn = _registry.get(name)
    if fn is None:
        raise KeyError(f"unknown job {name}")
    from services import metrics

    try:
        fn(**kwargs)
    except Exception:
        metrics.inc("por_jobs_total", {"name": name, "outcome": "failed"})
        raise
    metrics.inc("por_jobs_total", {"name": name, "outcome": "ok"})


def subscribe(event: str, handler: Callable[[dict], None]) -> None:
    if handler not in _subscribers.setdefault(event, []):
        _subscribers[event].append(handler)


def handler_name(fn: Callable) -> str:
    return f"{fn.__module__}.{fn.__qualname__}"


def run_handlers(event: str, payload: dict, only: str | None = None) -> list[tuple[str, Exception]]:
    """Run the subscribers for an event; returns the ones that failed so the caller can retry them."""
    from services import metrics

    failed = []
    for handler in list(_subscribers.get(event, [])):
        name = handler_name(handler)
        if only and name != only:
            continue
        try:
            handler(payload)
            metrics.inc("por_jobs_total", {"name": f"event:{event}", "outcome": "ok"})
        except Exception as exc:  # noqa: BLE001
            log.exception("event handler %s failed for %s", name, event)
            metrics.inc("por_jobs_total", {"name": f"event:{event}", "outcome": "failed"})
            failed.append((name, exc))
    return failed


def publish(event: str, payload: dict, session=None) -> None:
    """Publish a domain event. With a session, delivery waits until that session commits, so handlers see the
    committed state. Handlers run in-process, or on the worker when Redis jobs are on (never both)."""
    if session is not None:
        from services.hooks import on_commit

        if uses_redis():
            from db.models import OutboxMessage, new_id

            row_id = new_id()
            session.add(OutboxMessage(id=row_id, stream=EVENT_STREAM, fields=_event_fields(event, payload)))
            on_commit(session, lambda: relay_one(row_id))
            return
        on_commit(session, lambda: publish(event, payload))
        return
    if uses_redis():
        try:
            _xadd_with_retry(f"event {event}", EVENT_STREAM, _event_fields(event, payload), maxlen=EVENT_MAXLEN)
            return
        except Exception:  # noqa: BLE001
            log.error("EVENT NOT QUEUED: %s %s; Redis is unreachable, running its handlers in this process instead",
                      event, json.dumps(payload, default=str)[:500], exc_info=True)
    run_handlers(event, payload)


EVENT_MAXLEN = 100000
OUTBOX_RELAY_AFTER_SECONDS = 10  # the post-commit fast path gets this long before the relay task takes over
OUTBOX_KEEP_DAYS = 3


def _event_fields(event: str, payload: dict) -> dict:
    return {"event": event, "payload": json.dumps(payload, default=str), "attempt": "1"}


def _send_outbox_row(row) -> bool:
    """Copy one outbox row to its stream and mark it published. Returns False (and records why) on failure."""
    from db.models import utcnow

    try:
        _redis().xadd(row.stream, row.fields, maxlen=EVENT_MAXLEN)
    except Exception as exc:  # noqa: BLE001
        row.attempts += 1
        row.last_error = str(exc)[:1000]
        return False
    row.published_at = utcnow()
    row.last_error = None
    return True


def relay_one(row_id: str) -> None:
    """Post-commit fast path for one outbox row. On failure the row stays for relay_outbox."""
    from db.models import OutboxMessage
    from db.session import session_scope

    with session_scope() as s:
        row = s.get(OutboxMessage, row_id)
        if row is not None and row.published_at is None and not _send_outbox_row(row):
            log.warning("event %s not copied to Redis yet (%s); the outbox relay will retry it",
                        row.fields.get("event"), row.last_error)


def relay_outbox(limit: int = 500) -> int:
    """Copy outbox rows the fast path missed (crash after commit, Redis down) and prune old published rows.
    Runs as a periodic task; returns how many rows were copied."""
    if not uses_redis():
        return 0
    from datetime import timedelta

    from sqlalchemy import delete, select

    from db.models import OutboxMessage, utcnow
    from db.session import session_scope
    from services import metrics

    now = utcnow()
    sent = 0
    with session_scope() as s:
        rows = s.scalars(select(OutboxMessage).where(
            OutboxMessage.published_at.is_(None),
            OutboxMessage.created_at < now - timedelta(seconds=OUTBOX_RELAY_AFTER_SECONDS),
        ).order_by(OutboxMessage.created_at).limit(limit)).all()
        for row in rows:
            if not _send_outbox_row(row):
                metrics.inc("por_publish_failures_total", {"stream": row.stream})
                log.error("outbox relay could not reach Redis: %s; %s event(s) still waiting", row.last_error,
                          len(rows) - sent)
                break  # Redis is down: try again on the next tick
            sent += 1
        s.execute(delete(OutboxMessage).where(OutboxMessage.published_at.is_not(None),
                                              OutboxMessage.published_at < now - timedelta(days=OUTBOX_KEEP_DAYS)))
    if sent:
        log.info("outbox relay copied %s event(s) to Redis", sent)
    return sent


def backoff_seconds(attempt: int) -> int:
    return min(15 * (2 ** (attempt - 1)), 1800)


def dead_letter(r, stream: str, fields: dict, error: str) -> None:
    """Move a message to the dead-letter stream, keeping where it came from so it can be replayed."""
    r.xadd(DEAD_STREAM, {**fields, "stream": stream, "error": error[:1000], "failed_at": str(int(time.time()))},
           maxlen=10000)
    log.error("dead-lettered %s message after %s attempts: %s", stream, fields.get("attempt", "1"), error[:200])


def retry_later(r, stream: str, fields: dict, error: str) -> None:
    """Schedule another attempt of a failed message, or dead-letter it after POR_JOB_MAX_ATTEMPTS."""
    try:
        attempt = int(fields.get("attempt", "1"))
    except ValueError:
        attempt = 1
    if attempt >= get_settings().job_max_attempts:
        dead_letter(r, stream, fields, error)
        return
    msg = json.dumps({"stream": stream, "fields": {**fields, "attempt": str(attempt + 1)}})
    r.zadd(DELAYED, {msg: time.time() + backoff_seconds(attempt)})


def release_delayed(r, limit: int = 500) -> int:
    """Move retries that are due back onto their stream. Called by the worker loop."""
    due = r.zrangebyscore(DELAYED, 0, time.time(), start=0, num=limit)
    moved = 0
    for raw in due:
        if r.zrem(DELAYED, raw):  # only the replica that removes it re-queues it
            data = json.loads(raw)
            r.xadd(data["stream"], data["fields"])
            moved += 1
    return moved


def _load_jobs() -> None:
    # Importing registers @job functions.
    import services.mailer  # noqa: F401
    import services.pipeline  # noqa: F401
    import services.webhooks  # noqa: F401
