"""Logging and error reporting: plain text or JSON lines (POR_LOG_FORMAT), request ids, optional Sentry."""

from __future__ import annotations

import contextvars
import json
import logging

from services.config import get_settings

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("por_request_id", default=None)

_configured = False


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


def configure() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    s = get_settings()
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    if s.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    if s.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=s.sentry_dsn, environment=s.env, traces_sample_rate=0.0, send_default_pii=False)
            logging.getLogger("por").info("error reporting to Sentry is on")
        except ImportError:
            logging.getLogger("por").warning("POR_SENTRY_DSN is set but sentry-sdk is not installed (pip install sentry-sdk)")
