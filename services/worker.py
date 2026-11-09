"""Background worker: consumes jobs and events from Redis Streams and runs the periodic tasks.

- Jobs and event handlers that fail are retried with backoff, then moved to the dead-letter stream (por:dead).
- Messages left pending by a crashed worker are reclaimed after a few minutes.
- Periodic tasks (services/scheduler.py) take a lock, so several replicas can run safely.
Run: python -m services.worker
"""

from __future__ import annotations

import json
import logging
import socket
import time
import uuid

from services import jobs, scheduler
from services.config import get_settings

log = logging.getLogger("por.worker")

GROUP = "por-workers"
RECLAIM_IDLE_MS = 5 * 60 * 1000


def _ensure_group(r, stream: str) -> None:
    try:
        r.xgroup_create(stream, GROUP, id="0", mkstream=True)
    except Exception:  # noqa: BLE001  (group already exists)
        pass


def handle(r, stream: str, msg_id, fields: dict) -> None:
    """Process one stream message; failures are scheduled for retry rather than lost."""
    try:
        if stream == jobs.JOB_STREAM:
            try:
                jobs.run(fields["name"], json.loads(fields["kwargs"]))
            except Exception as exc:  # noqa: BLE001
                log.exception("job %s failed", fields.get("name"))
                jobs.retry_later(r, stream, fields, f"{type(exc).__name__}: {exc}")
        else:
            payload = json.loads(fields["payload"])
            for name, exc in jobs.run_handlers(fields["event"], payload, only=fields.get("only")):
                # Retry only the handler that failed, so the others are not run twice.
                jobs.retry_later(r, stream, {**fields, "only": name}, f"{type(exc).__name__}: {exc}")
    finally:
        r.xack(stream, GROUP, msg_id)


def _decode(fields: dict) -> dict:
    return {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in fields.items()}


def reclaim(r, consumer: str) -> int:
    """Take over messages another consumer read but never acknowledged (it crashed)."""
    n = 0
    for stream in (jobs.JOB_STREAM, jobs.EVENT_STREAM):
        try:
            res = r.xautoclaim(stream, GROUP, consumer, min_idle_time=RECLAIM_IDLE_MS, start_id="0-0", count=50)
        except Exception:  # noqa: BLE001  (older Redis without XAUTOCLAIM)
            continue
        for msg_id, fields in (res[1] if len(res) > 1 else []):
            if fields:
                handle(r, stream, msg_id, _decode(fields))
                n += 1
    return n


def run_forever() -> None:
    import connectors

    connectors.register()
    s = get_settings()
    interval = s.scheduler_interval_seconds
    if not s.redis_url:
        log.info("no POR_REDIS_URL: running periodic tasks only")
        while True:
            scheduler.periodic()
            time.sleep(interval)
    r = jobs._redis()
    consumer = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    for stream in (jobs.JOB_STREAM, jobs.EVENT_STREAM):
        _ensure_group(r, stream)
    last = 0.0
    while True:
        if time.monotonic() - last > interval:
            scheduler.periodic()
            jobs.release_delayed(r)
            reclaim(r, consumer)
            last = time.monotonic()
        batches = r.xreadgroup(GROUP, consumer, {jobs.JOB_STREAM: ">", jobs.EVENT_STREAM: ">"}, count=10, block=5000) or []
        for stream, messages in batches:
            stream = stream.decode() if isinstance(stream, bytes) else stream
            for msg_id, fields in messages:
                handle(r, stream, msg_id, _decode(fields))


if __name__ == "__main__":
    from services.logging_setup import configure

    configure()
    run_forever()
