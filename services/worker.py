"""Background worker: consumes jobs and events from Redis Streams and runs periodic tasks.

Periodic: webhook retries, SLA escalation, assignment of queued items, SharePoint folder polling.
Run: python -m services.worker
"""

from __future__ import annotations

import json
import logging
import socket
import time

from sqlalchemy import select

from db.models import ConnectorConfig
from db.session import session_scope
from services import jobs, webhooks, workflow
from services.config import get_settings

log = logging.getLogger("por.worker")

GROUP = "por-workers"


def periodic() -> None:
    try:
        webhooks.retry_due()
    except Exception:  # noqa: BLE001
        log.exception("webhook retries failed")
    try:
        with session_scope() as s:
            workflow.check_slas(s)
            workflow.assign_pending(s)
    except Exception:  # noqa: BLE001
        log.exception("workflow tick failed")
    try:
        from connectors import sharepoint

        with session_scope() as s:
            for cfg in s.scalars(select(ConnectorConfig).where(ConnectorConfig.kind == "sharepoint", ConnectorConfig.active.is_(True))).all():
                sharepoint.run_once(s, cfg)
    except Exception:  # noqa: BLE001
        log.exception("sharepoint polling failed")


def _ensure_group(r, stream: str) -> None:
    try:
        r.xgroup_create(stream, GROUP, id="0", mkstream=True)
    except Exception:  # noqa: BLE001  (group already exists)
        pass


def run_forever(interval: float = 15.0) -> None:
    import connectors

    connectors.register()
    s = get_settings()
    last = 0.0
    if not s.redis_url:
        log.info("no POR_REDIS_URL: running periodic tasks only")
        while True:
            periodic()
            time.sleep(interval)
    r = jobs._redis()
    consumer = f"{socket.gethostname()}-{id(r)}"
    for stream in (jobs.JOB_STREAM, jobs.EVENT_STREAM):
        _ensure_group(r, stream)
    while True:
        if time.monotonic() - last > interval:
            periodic()
            last = time.monotonic()
        batches = r.xreadgroup(GROUP, consumer, {jobs.JOB_STREAM: ">", jobs.EVENT_STREAM: ">"}, count=10, block=5000) or []
        for stream, messages in batches:
            stream = stream.decode() if isinstance(stream, bytes) else stream
            for msg_id, fields in messages:
                f = {k.decode(): v.decode() for k, v in fields.items()}
                try:
                    if stream == jobs.JOB_STREAM:
                        jobs.run(f["name"], json.loads(f["kwargs"]))
                    else:
                        payload = json.loads(f["payload"])
                        for handler in jobs._subscribers.get(f["event"], []):
                            handler(payload)
                except Exception:  # noqa: BLE001
                    log.exception("failed processing %s %s", stream, msg_id)
                r.xack(stream, GROUP, msg_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_forever()
