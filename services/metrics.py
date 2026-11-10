"""Prometheus metrics without extra dependencies: counters and histograms in process memory, rendered in the
text exposition format at GET /metrics, plus gauges read from the database (and, when Redis is configured, from
the job streams) at scrape time. The worker serves the same output on POR_WORKER_METRICS_PORT."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_counters: dict[tuple[str, tuple], float] = {}
_hist: dict[tuple[str, tuple], list] = {}  # key -> [bucket counts..., sum, count]
BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
HELP = {
    "por_http_requests_total": "HTTP requests by method, route and status.",
    "por_http_request_duration_seconds": "HTTP request latency.",
    "por_jobs_total": "Background jobs and event handlers by outcome.",
    "por_webhook_attempts_total": "Webhook delivery attempts by outcome.",
    "por_model_calls_total": "Model gateway calls by provider and outcome.",
    "por_model_fallbacks_total": "Model answers that could not be used, so the heuristic engine or templates were used instead.",
    "por_rate_limited_total": "Requests refused by a rate limit.",
    "por_emails_total": "Emails sent or written to the outbox.",
    "por_publish_failures_total": "Jobs or events that could not be written to Redis after retries.",
}


def _key(name: str, labels: dict | None) -> tuple[str, tuple]:
    return name, tuple(sorted((labels or {}).items()))


def inc(name: str, labels: dict | None = None, value: float = 1.0) -> None:
    k = _key(name, labels)
    with _lock:
        _counters[k] = _counters.get(k, 0.0) + value


def observe(name: str, value: float, labels: dict | None = None) -> None:
    k = _key(name, labels)
    with _lock:
        h = _hist.setdefault(k, [0] * len(BUCKETS) + [0.0, 0])
        for i, b in enumerate(BUCKETS):
            if value <= b:
                h[i] += 1
        h[-2] += value
        h[-1] += 1


def reset() -> None:
    with _lock:
        _counters.clear()
        _hist.clear()


def _escape(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt_labels(labels: tuple, extra: dict | None = None) -> str:
    items = list(labels) + list((extra or {}).items())
    if not items:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in items) + "}"


def _gauges() -> list[tuple[str, str, dict, float]]:
    from sqlalchemy import func, select

    from db.models import ReviewTask, Submission, WebhookDelivery
    from db.session import session_scope

    out = []
    with session_scope() as s:
        for status, n in s.execute(select(Submission.status, func.count()).group_by(Submission.status)).all():
            out.append(("por_submissions", "Submissions by status.", {"status": status}, n))
        held = s.scalar(select(func.count()).select_from(Submission).where(Submission.hold_reason.is_not(None))) or 0
        out.append(("por_submissions_held", "Submissions held for oversight or capacity.", {}, held))
        open_tasks = s.scalar(select(func.count()).select_from(ReviewTask).where(ReviewTask.status == "assigned")) or 0
        out.append(("por_review_tasks_open", "Open review tasks.", {}, open_tasks))
        pending = s.scalar(select(func.count()).select_from(WebhookDelivery).where(WebhookDelivery.status == "pending")) or 0
        out.append(("por_webhooks_pending", "Webhook deliveries waiting for a retry.", {}, pending))
    return out


def _stream_gauges() -> list[tuple[str, str, dict, float]]:
    """Backlog of the Redis job streams, the delayed retries and the dead-letter stream."""
    from services.config import get_settings

    if not get_settings().redis_url:
        return []
    from services import jobs
    from services.worker import GROUP

    r = jobs._redis()
    out = []
    for stream in (jobs.JOB_STREAM, jobs.EVENT_STREAM, jobs.DEAD_STREAM):
        out.append(("por_stream_length", "Entries in a Redis stream (handled ones included, up to its cap).",
                    {"stream": stream}, r.xlen(stream)))
    for stream in (jobs.JOB_STREAM, jobs.EVENT_STREAM):
        try:
            groups = r.xinfo_groups(stream)
        except Exception:  # noqa: BLE001  (stream or group not created yet)
            groups = []
        for g in groups:
            name = g.get("name")
            if (name.decode() if isinstance(name, bytes) else name) != GROUP:
                continue
            out.append(("por_stream_pending", "Messages read by a worker but not yet acknowledged.",
                        {"stream": stream}, g.get("pending") or 0))
            if g.get("lag") is not None:
                out.append(("por_stream_lag", "Messages not yet read by any worker.", {"stream": stream}, g["lag"]))
    out.append(("por_dead_letters", "Messages in the dead-letter stream (python -m services.worker dead list).", {},
                r.xlen(jobs.DEAD_STREAM)))
    out.append(("por_delayed_retries", "Failed jobs and handlers waiting for their next attempt.", {},
                r.zcard(jobs.DELAYED)))
    return out


def _render_gauges(lines: list[str], seen: set[str], gauges) -> None:
    for name, help_text, labels, v in gauges:
        if name not in seen:
            lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
            seen.add(name)
        lines.append(f"{name}{_fmt_labels(tuple(sorted(labels.items())))} {v}")


def render() -> str:
    lines: list[str] = []
    with _lock:
        counters = dict(_counters)
        hist = {k: list(v) for k, v in _hist.items()}
    seen: set[str] = set()
    for (name, labels), v in sorted(counters.items()):
        if name not in seen:
            lines += [f"# HELP {name} {HELP.get(name, name)}", f"# TYPE {name} counter"]
            seen.add(name)
        lines.append(f"{name}{_fmt_labels(labels)} {v}")
    for (name, labels), h in sorted(hist.items()):
        if name not in seen:
            lines += [f"# HELP {name} {HELP.get(name, name)}", f"# TYPE {name} histogram"]
            seen.add(name)
        for i, b in enumerate(BUCKETS):
            lines.append(f"{name}_bucket{_fmt_labels(labels, {'le': b})} {h[i]}")
        lines.append(f"{name}_bucket{_fmt_labels(labels, {'le': '+Inf'})} {h[-1]}")
        lines.append(f"{name}_sum{_fmt_labels(labels)} {h[-2]}")
        lines.append(f"{name}_count{_fmt_labels(labels)} {h[-1]}")
    try:
        _render_gauges(lines, seen, _gauges())
    except Exception:  # noqa: BLE001  (database unavailable: still serve process metrics)
        lines.append("# database gauges unavailable")
    try:
        _render_gauges(lines, seen, _stream_gauges())
    except Exception:  # noqa: BLE001  (Redis unavailable: still serve the rest)
        lines.append("# redis stream gauges unavailable")
    return "\n".join(lines) + "\n"
