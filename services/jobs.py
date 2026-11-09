"""Job runner and event bus.

Inline mode (dev/test, or no Redis) runs jobs and event handlers immediately in-process. With Redis jobs on,
jobs and events go onto Redis Streams and are handled only by `python -m services.worker`, so each handler runs
exactly once per event however many API replicas there are. Failed jobs and handlers are retried with backoff
(POR_JOB_MAX_ATTEMPTS) and then moved to the dead-letter stream.
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


def _redis():
    import redis

    return redis.Redis.from_url(get_settings().redis_url)


def uses_redis() -> bool:
    s = get_settings()
    return bool(s.redis_url) and not s.inline_jobs


def enqueue(name: str, **kwargs) -> None:
    if not uses_redis():
        run(name, kwargs)
        return
    _redis().xadd(JOB_STREAM, {"name": name, "kwargs": json.dumps(kwargs, default=str), "attempt": "1"})


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

        on_commit(session, lambda: publish(event, payload))
        return
    if uses_redis():
        try:
            _redis().xadd(EVENT_STREAM, {"event": event, "payload": json.dumps(payload, default=str), "attempt": "1"},
                          maxlen=100000)
            return
        except Exception:  # noqa: BLE001
            log.exception("failed to publish %s to redis; running handlers in-process instead", event)
    run_handlers(event, payload)


def backoff_seconds(attempt: int) -> int:
    return min(15 * (2 ** (attempt - 1)), 1800)


def retry_later(r, stream: str, fields: dict, error: str) -> None:
    """Schedule another attempt of a failed message, or dead-letter it after POR_JOB_MAX_ATTEMPTS."""
    attempt = int(fields.get("attempt", "1"))
    if attempt >= get_settings().job_max_attempts:
        r.xadd(DEAD_STREAM, {**fields, "stream": stream, "error": error[:1000], "failed_at": str(int(time.time()))},
               maxlen=10000)
        log.error("dead-lettered %s message after %s attempts: %s", stream, attempt, error[:200])
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
