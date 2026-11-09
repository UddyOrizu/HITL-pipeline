"""Periodic tasks: webhook retries, SLA checks, queued assignments, stuck-submission recovery, connector polling,
retention and token cleanup.

The worker runs them; when jobs run inline (no separate worker) the API runs them on a background thread instead.
Each task takes a lock first (Redis SET NX when Redis is configured, otherwise a process lock), so several worker
replicas never run the same task at the same time.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import delete, select

from db.models import ConnectorConfig, RevokedToken, Submission, utcnow
from db.session import session_scope
from services.config import get_settings

log = logging.getLogger("por.scheduler")

_local_locks: dict[str, threading.Lock] = {}
_last_run: dict[str, float] = {}


def _acquire(name: str, ttl: int) -> Callable[[], None] | None:
    s = get_settings()
    if s.redis_url:
        try:
            from services.jobs import _redis

            r = _redis()
            token = uuid.uuid4().hex
            if not r.set(f"por:lock:{name}", token, nx=True, ex=ttl):
                return None

            def release():
                try:
                    if r.get(f"por:lock:{name}") == token.encode():
                        r.delete(f"por:lock:{name}")
                except Exception:  # noqa: BLE001
                    pass

            return release
        except Exception:  # noqa: BLE001
            log.warning("redis lock unavailable for %s; using a process lock", name)
    lock = _local_locks.setdefault(name, threading.Lock())
    if not lock.acquire(blocking=False):
        return None
    return lock.release


def run_task(name: str, fn: Callable[[], object], every_seconds: float = 0, lock_ttl: int = 300) -> bool:
    """Run fn under the task lock, at most once per every_seconds in this process. Returns True if it ran."""
    now = time.monotonic()
    if every_seconds and now - _last_run.get(name, -1e9) < every_seconds:
        return False
    release = _acquire(name, lock_ttl)
    if release is None:
        return False
    _last_run[name] = now
    try:
        fn()
    except Exception:  # noqa: BLE001
        log.exception("periodic task %s failed", name)
    finally:
        release()
    return True


# --- Tasks ------------------------------------------------------------------------------


def retry_webhooks() -> None:
    from services import webhooks

    webhooks.retry_due()


def workflow_tick() -> None:
    from services import workflow

    with session_scope() as s:
        workflow.check_slas(s)
        workflow.assign_pending(s)


def recover_submissions() -> None:
    """Re-queue processing retries that are due, and submissions whose job was lost (crash, Redis restart)."""
    from services import jobs

    now = utcnow()
    stale = now - timedelta(minutes=get_settings().stuck_after_minutes)
    ids: list[tuple[str, str]] = []
    with session_scope() as s:
        for sub in s.scalars(select(Submission).where(Submission.status == "processing", Submission.updated_at < stale)
                             .limit(100)).all():
            # The worker died mid-way: put it back so it is processed again.
            sub.status = "received"
            sub.retry_at = now
            from services import audit

            audit.record(s, sub.tenant_id, "system:scheduler", "submission.processing_requeued", "submission", sub.id,
                         {"reason": "stuck_in_processing"})
        s.flush()
        rows = s.scalars(select(Submission).where(Submission.status == "received").limit(200)).all()
        for sub in rows:
            due = sub.retry_at <= now if sub.retry_at else sub.updated_at < stale
            if due:
                sub.retry_at = None
                ids.append((sub.id, sub.tenant_id))
    for sid, tid in ids:
        jobs.enqueue("process_submission", submission_id=sid, tenant_id=tid)


def poll_connectors() -> None:
    """Run each polling connector on its own, so one failing configuration never stops the others."""
    from connectors import poll_one

    with session_scope() as s:
        ids = [c.id for c in s.scalars(select(ConnectorConfig).where(ConnectorConfig.active.is_(True))).all()]
    for cid in ids:
        poll_one(cid)


def retention_sweep() -> None:
    from services import retention

    retention.sweep()


def cleanup_tokens() -> None:
    with session_scope() as s:
        s.execute(delete(RevokedToken).where(RevokedToken.expires_at < utcnow()))


TASKS: list[tuple[str, Callable[[], None], float]] = [
    ("webhook_retries", retry_webhooks, 0),
    ("workflow_tick", workflow_tick, 0),
    ("recover_submissions", recover_submissions, 60),
    ("poll_connectors", poll_connectors, 60),
    ("retention_sweep", retention_sweep, 3600),
    ("cleanup_tokens", cleanup_tokens, 3600),
]


def periodic() -> None:
    for name, fn, every in TASKS:
        run_task(name, fn, every)


# --- In-API scheduler (inline mode) ------------------------------------------------------

_thread: threading.Thread | None = None
_stop = threading.Event()


def start_background() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    interval = get_settings().scheduler_interval_seconds

    def loop():
        log.info("running periodic tasks in the API process every %ss (no separate worker)", interval)
        while not _stop.wait(interval):
            periodic()

    _thread = threading.Thread(target=loop, name="por-scheduler", daemon=True)
    _thread.start()


def stop_background() -> None:
    _stop.set()
