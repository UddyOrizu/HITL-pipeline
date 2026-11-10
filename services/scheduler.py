"""Periodic tasks: webhook retries, SLA checks and reminders, queued assignments, stuck-submission recovery,
connector polling, retention and token cleanup.

The worker runs them; when jobs run inline (no separate worker) the API runs them on a background thread instead.
Each task takes a lock first (Redis SET NX when Redis is configured, otherwise a process lock), so several worker
replicas never run the same task at the same time. With Redis, "every N seconds" is cluster-wide: the last run
time is kept in Redis, so adding replicas does not make a task run more often.

Polling connectors run on their own, on a small thread pool, each under its own lock (por:lock:connector:<id>,
renewed while it runs): one slow connector never delays the others, and two replicas never poll the same
connector at once. The manual Run button in the integrations console takes the same lock.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import delete, or_, select

from db.models import ConnectorConfig, PasswordResetToken, RevokedToken, Submission, utcnow
from db.session import session_scope
from services.config import get_settings

log = logging.getLogger("por.scheduler")

_local_locks: dict[str, threading.Lock] = {}
_last_run: dict[str, float] = {}  # wall-clock time of the last run in this process (fallback without Redis)


def _redis_or_none():
    if not get_settings().redis_url:
        return None
    from services.jobs import _redis

    return _redis()


def acquire(name: str, ttl: int, renew: bool = False) -> Callable[[], None] | None:
    """Take the lock for a task. Returns a release function, or None when someone else holds it.

    renew: keep extending the Redis lock while it is held (for work that may outlast ttl, like a slow connector),
    so the lock never expires under a task that is still running, yet frees itself within ttl if the process dies."""
    try:
        r = _redis_or_none()
        if r is not None:
            key = f"por:lock:{name}"
            token = uuid.uuid4().hex
            if not r.set(key, token, nx=True, ex=ttl):
                return None
            stop = threading.Event()
            if renew:
                def keep_alive():
                    while not stop.wait(max(1.0, ttl / 3)):
                        try:
                            if r.get(key) != token.encode():
                                return
                            r.expire(key, ttl)
                        except Exception:  # noqa: BLE001
                            log.warning("could not renew lock %s", name)

                threading.Thread(target=keep_alive, name=f"lock-{name}", daemon=True).start()

            def release():
                stop.set()
                try:
                    if r.get(key) == token.encode():
                        r.delete(key)
                except Exception:  # noqa: BLE001
                    pass

            return release
    except Exception:  # noqa: BLE001
        log.warning("redis lock unavailable for %s; using a process lock", name)
    lock = _local_locks.setdefault(name, threading.Lock())
    if not lock.acquire(blocking=False):
        return None
    return lock.release


_acquire = acquire  # older name


def _ran_recently(name: str, every_seconds: float) -> bool:
    """Whether the task ran less than every_seconds ago anywhere in the cluster (in this process without Redis)."""
    now = time.time()
    if now - _last_run.get(name, -1e12) < every_seconds:
        return True
    try:
        r = _redis_or_none()
        if r is not None:
            last = r.get(f"por:lastrun:{name}")
            if last is not None and now - float(last) < every_seconds:
                return True
    except Exception:  # noqa: BLE001
        log.warning("redis unavailable for the last run of %s; using this process's clock", name)
    return False


def _mark_ran(name: str, every_seconds: float) -> None:
    now = time.time()
    _last_run[name] = now
    if not every_seconds:
        return
    try:
        r = _redis_or_none()
        if r is not None:
            r.set(f"por:lastrun:{name}", repr(now), ex=int(every_seconds * 2) + 60)
    except Exception:  # noqa: BLE001
        pass


def now_too_soon(name: str, every_seconds: float) -> bool:
    """Cheap check without Redis: this process ran the task recently."""
    return time.time() - _last_run.get(name, -1e12) < every_seconds


def run_task(name: str, fn: Callable[[], object], every_seconds: float = 0, lock_ttl: int = 300,
             renew: bool = False) -> bool:
    """Run fn under the task lock, at most once per every_seconds across the cluster. Returns True if it ran."""
    if every_seconds and now_too_soon(name, every_seconds):
        return False
    release = acquire(name, lock_ttl, renew=renew)
    if release is None:
        return False
    try:
        # Checked again under the lock: another replica may have just finished a run.
        if every_seconds and _ran_recently(name, every_seconds):
            return False
        _mark_ran(name, every_seconds)
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("periodic task %s failed", name)
    finally:
        release()
    return True


# --- Tasks ------------------------------------------------------------------------------


def relay_outbox() -> None:
    """Copy committed events the post-commit fast path missed to Redis Streams (see services/jobs.py)."""
    from services import jobs

    jobs.relay_outbox()


def retry_webhooks() -> None:
    from services import webhooks

    webhooks.retry_due()


def workflow_tick() -> None:
    from services import workflow

    with session_scope() as s:
        workflow.check_slas(s)
        workflow.assign_pending(s)


def sla_reminders() -> None:
    """Teams nudges for tasks past 75% of their stage SLA without a decision."""
    from connectors import teams

    teams.send_sla_nudges()


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


CONNECTOR_EVERY_SECONDS = 60
CONNECTOR_THREADS = 4
_pool: ThreadPoolExecutor | None = None
_in_flight: dict[str, Future] = {}
_pool_lock = threading.Lock()


def poll_connector(connector_id: str) -> bool:
    """Poll one connector if it is due (cluster-wide) and free. Returns True if it ran."""
    from connectors import LOCK_TTL, lock_name, poll_one

    return run_task(lock_name(connector_id), lambda: poll_one(connector_id, lock=False),
                    every_seconds=CONNECTOR_EVERY_SECONDS, lock_ttl=LOCK_TTL, renew=True)


def poll_connectors(wait: bool = False) -> None:
    """Hand each active connector to the thread pool, so each is polled on its own schedule and under its own
    lock. A connector still running in this process is not queued again."""
    global _pool
    from connectors import lock_name, polls

    with session_scope() as s:
        ids = [c.id for c in s.scalars(select(ConnectorConfig).where(ConnectorConfig.active.is_(True))).all() if polls(c)]
    futures = []
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=CONNECTOR_THREADS, thread_name_prefix="por-connector")
        for cid in ids:
            running = _in_flight.get(cid)
            if running is not None and not running.done():
                continue
            if now_too_soon(lock_name(cid), CONNECTOR_EVERY_SECONDS):
                continue
            _in_flight[cid] = fut = _pool.submit(poll_connector, cid)
            futures.append(fut)
        for cid in [c for c, f in _in_flight.items() if f.done()]:
            _in_flight.pop(cid, None)
    if wait:
        for fut in futures:
            fut.result()


def retention_sweep() -> None:
    from services import retention

    retention.sweep()


def cleanup_tokens() -> None:
    """Drop revoked sign-in tokens past their expiry, and password reset links that were used or have expired."""
    now = utcnow()
    with session_scope() as s:
        s.execute(delete(RevokedToken).where(RevokedToken.expires_at < now))
        s.execute(delete(PasswordResetToken).where(or_(PasswordResetToken.expires_at < now,
                                                       PasswordResetToken.used_at.is_not(None))))


TASKS: list[tuple[str, Callable[[], None], float]] = [
    ("relay_outbox", relay_outbox, 0),
    ("webhook_retries", retry_webhooks, 0),
    ("workflow_tick", workflow_tick, 0),
    ("sla_reminders", sla_reminders, 300),
    ("recover_submissions", recover_submissions, 60),
    ("poll_connectors", poll_connectors, 15),
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
