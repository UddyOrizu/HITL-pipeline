"""Background worker: consumes jobs and events from Redis Streams and runs the periodic tasks.

- Jobs and event handlers that fail are retried with backoff, then moved to the dead-letter stream (por:dead).
  Malformed messages go there at once.
- Messages left pending by a crashed worker are reclaimed after a few minutes; each crash counts as an attempt, so
  a message that keeps killing workers is dead-lettered after POR_JOB_MAX_ATTEMPTS.
- Periodic tasks (services/scheduler.py) take a lock, so several replicas can run safely.
- Redis outages are logged and retried with backoff; the worker keeps running.
- A heartbeat file is touched every loop (`python -m services.worker health` checks it, for container health
  checks), and POR_WORKER_METRICS_PORT serves Prometheus metrics, including stream lengths, at /metrics.

Run:      python -m services.worker
Health:   python -m services.worker health
Dead letters:
          python -m services.worker dead list [N]        the latest N (default 20) dead-lettered messages
          python -m services.worker dead replay <id>|all  put messages back on their stream with a fresh attempt count
          python -m services.worker dead purge [<id>]    delete one, or all, dead-lettered messages
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from services import jobs, scheduler
from services.config import get_settings

log = logging.getLogger("por.worker")

GROUP = "por-workers"
RECLAIM_IDLE_MS = 5 * 60 * 1000
HEARTBEAT_FILE = Path(os.environ.get("POR_WORKER_HEARTBEAT_FILE") or Path(tempfile.gettempdir()) / "por-worker.heartbeat")
HEARTBEAT_MAX_AGE = 120  # seconds; the loop blocks at most 5 s on Redis, plus the periodic tasks
MAX_BACKOFF = 30


def _ensure_group(r, stream: str) -> None:
    try:
        r.xgroup_create(stream, GROUP, id="0", mkstream=True)
    except Exception as exc:  # noqa: BLE001
        if "BUSYGROUP" not in str(exc):  # BUSYGROUP: the group already exists
            raise


def _parse(stream: str, fields: dict) -> tuple:
    """Validate a message before running it. Raises ValueError for one that can never succeed."""
    try:
        if stream == jobs.JOB_STREAM:
            kwargs = json.loads(fields["kwargs"])
            if not isinstance(kwargs, dict):
                raise ValueError("kwargs is not an object")
            return fields["name"], kwargs
        payload = json.loads(fields["payload"])
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        return fields["event"], payload
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed message: {type(exc).__name__}: {exc}") from None


def handle(r, stream: str, msg_id, fields: dict) -> None:
    """Process one stream message; failures are scheduled for retry rather than lost."""
    try:
        try:
            name, data = _parse(stream, fields)
        except ValueError as exc:
            jobs.dead_letter(r, stream, fields, str(exc))  # retrying cannot fix it
            return
        if stream == jobs.JOB_STREAM:
            try:
                jobs.run(name, data)
            except Exception as exc:  # noqa: BLE001
                log.exception("job %s failed", name)
                jobs.retry_later(r, stream, fields, f"{type(exc).__name__}: {exc}")
        else:
            for handler, exc in jobs.run_handlers(name, data, only=fields.get("only")):
                # Retry only the handler that failed, so the others are not run twice.
                jobs.retry_later(r, stream, {**fields, "only": handler}, f"{type(exc).__name__}: {exc}")
    finally:
        r.xack(stream, GROUP, msg_id)


def _decode(fields: dict) -> dict:
    return {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in fields.items()}


def _deliveries(r, stream: str, msg_id) -> int:
    """How many times the group has delivered this message (1 for a first delivery)."""
    try:
        info = r.xpending_range(stream, GROUP, min=msg_id, max=msg_id, count=1)
        return int(info[0]["times_delivered"]) if info else 1
    except Exception:  # noqa: BLE001
        return 1


def reclaim(r, consumer: str) -> int:
    """Take over messages another consumer read but never acknowledged (it crashed). Each earlier delivery counts
    as a failed attempt, so a message that crashes every worker that reads it ends up dead-lettered."""
    n = 0
    max_attempts = get_settings().job_max_attempts
    for stream in (jobs.JOB_STREAM, jobs.EVENT_STREAM):
        try:
            res = r.xautoclaim(stream, GROUP, consumer, min_idle_time=RECLAIM_IDLE_MS, start_id="0-0", count=50)
        except Exception as exc:  # noqa: BLE001  (older Redis without XAUTOCLAIM)
            log.debug("xautoclaim unavailable on %s: %s", stream, exc)
            continue
        for msg_id, fields in (res[1] if len(res) > 1 else []):
            if not fields:  # deleted (trimmed) while pending: nothing to run
                r.xack(stream, GROUP, msg_id)
                continue
            fields = _decode(fields)
            try:
                base = int(fields.get("attempt", "1"))
            except ValueError:
                base = 1
            attempt = base + max(0, _deliveries(r, stream, msg_id) - 1)
            fields["attempt"] = str(attempt)
            if attempt > max_attempts:
                jobs.dead_letter(r, stream, fields, "worker stopped while handling it (crashed or killed) too often")
                r.xack(stream, GROUP, msg_id)
            else:
                log.warning("reclaimed %s message %s from a stopped worker (attempt %s)", stream, msg_id, attempt)
                handle(r, stream, msg_id, fields)
            n += 1
    return n


def heartbeat() -> None:
    try:
        HEARTBEAT_FILE.touch()
    except OSError:
        log.warning("cannot write the heartbeat file %s", HEARTBEAT_FILE)


def healthy(max_age: float = HEARTBEAT_MAX_AGE) -> bool:
    try:
        return time.time() - HEARTBEAT_FILE.stat().st_mtime < max_age
    except OSError:
        return False


def _is_redis_error(exc: Exception) -> bool:
    try:
        import redis

        return isinstance(exc, redis.exceptions.ConnectionError | redis.exceptions.TimeoutError)
    except ImportError:
        return False


def run_forever() -> None:
    import connectors

    connectors.register()
    s = get_settings()
    interval = s.scheduler_interval_seconds
    if s.worker_metrics_port:
        start_metrics_server(s.worker_metrics_port)
    if not s.redis_url:
        log.info("no POR_REDIS_URL: running periodic tasks only")
        while True:
            heartbeat()
            scheduler.periodic()
            time.sleep(interval)
    r = jobs._redis()
    consumer = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    groups_ready = False
    last = 0.0
    failures = 0
    while True:
        heartbeat()
        try:
            if not groups_ready:
                for stream in (jobs.JOB_STREAM, jobs.EVENT_STREAM):
                    _ensure_group(r, stream)
                groups_ready = True
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
            failures = 0
        except Exception as exc:  # noqa: BLE001
            failures += 1
            delay = min(MAX_BACKOFF, 2 ** min(failures - 1, 5))
            if "NOGROUP" in str(exc):
                groups_ready = False  # Redis restarted without its data: create the groups again
            if _is_redis_error(exc) or "NOGROUP" in str(exc):
                log.error("redis unavailable (%s); retrying in %ss", exc, delay)
            else:
                log.exception("worker loop failed; retrying in %ss", delay)
            time.sleep(delay)


# --- Metrics ----------------------------------------------------------------------------


def start_metrics_server(port: int) -> threading.Thread:
    """Serve GET /metrics (Prometheus text format) on a background thread. POR_METRICS_TOKEN applies as on the API."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from services import metrics
    from services.security import constant_time_equal

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            s = get_settings()
            if self.path.split("?")[0] != "/metrics" or not s.metrics_enabled:
                self._reply(404, b"not found\n")
                return
            if s.metrics_token and not constant_time_equal(self.headers.get("Authorization", ""), f"Bearer {s.metrics_token}"):
                self._reply(401, b"metrics token required\n")
                return
            self._reply(200, metrics.render().encode(), "text/plain; version=0.0.4")

        def _reply(self, status: int, body: bytes, content_type: str = "text/plain") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # scrapes are not worth a log line each
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104  (inside the container network)
    thread = threading.Thread(target=server.serve_forever, name="por-worker-metrics", daemon=True)
    thread.start()
    log.info("worker metrics on :%s/metrics", port)
    return thread


# --- Dead-letter CLI --------------------------------------------------------------------

DEAD_ONLY_FIELDS = ("stream", "error", "failed_at")


def dead_list(r, count: int = 20) -> list[tuple[str, dict]]:
    rows = r.xrevrange(jobs.DEAD_STREAM, count=count)
    return [(i.decode() if isinstance(i, bytes) else i, _decode(f)) for i, f in rows]


def dead_replay(r, msg_id: str) -> int:
    """Put dead-lettered messages (one id, or "all") back on their stream with a fresh attempt count."""
    if msg_id == "all":
        rows = [(i, _decode(f)) for i, f in r.xrange(jobs.DEAD_STREAM)]
    else:
        rows = [(i, _decode(f)) for i, f in r.xrange(jobs.DEAD_STREAM, min=msg_id, max=msg_id)]
    n = 0
    for i, fields in rows:
        stream = fields.get("stream")
        if stream not in (jobs.JOB_STREAM, jobs.EVENT_STREAM):
            continue
        r.xadd(stream, {**{k: v for k, v in fields.items() if k not in DEAD_ONLY_FIELDS}, "attempt": "1"})
        r.xdel(jobs.DEAD_STREAM, i)
        n += 1
    return n


def dead_purge(r, msg_id: str | None = None) -> int:
    if msg_id:
        return int(r.xdel(jobs.DEAD_STREAM, msg_id))
    n = int(r.xlen(jobs.DEAD_STREAM))
    r.delete(jobs.DEAD_STREAM)
    return n


def cli(argv: list[str]) -> int:
    if argv[:1] == ["health"]:
        ok = healthy()
        print("ok" if ok else f"unhealthy: no heartbeat in {HEARTBEAT_FILE} for {HEARTBEAT_MAX_AGE}s")
        return 0 if ok else 1
    if argv[:1] != ["dead"] or len(argv) < 2 or argv[1] not in ("list", "replay", "purge"):
        print(__doc__)
        return 2
    if not get_settings().redis_url:
        print("POR_REDIS_URL is not set: there is no dead-letter stream")
        return 1
    r = jobs._redis()
    action, arg = argv[1], (argv[2] if len(argv) > 2 else None)
    if action == "list":
        for i, f in dead_list(r, int(arg or 20)):
            what = f.get("name") or f.get("event")
            only = f" handler={f['only']}" if f.get("only") else ""
            print(f"{i}  {f.get('stream')}  {what}{only}  attempts={f.get('attempt')}  failed_at={f.get('failed_at')}")
            print(f"    error: {f.get('error', '')[:300]}")
            print(f"    data:  {(f.get('kwargs') or f.get('payload') or '')[:300]}")
        return 0
    if action == "replay":
        if not arg:
            print("usage: python -m services.worker dead replay <id>|all")
            return 2
        print(f"replayed {dead_replay(r, arg)} message(s)")
        return 0
    print(f"purged {dead_purge(r, arg)} message(s)")
    return 0


if __name__ == "__main__":
    from services.logging_setup import configure

    configure()
    if len(sys.argv) > 1:
        raise SystemExit(cli(sys.argv[1:]))
    run_forever()
