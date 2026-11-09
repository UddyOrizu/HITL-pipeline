"""Prometheus metrics without extra dependencies: counters and histograms in process memory, rendered in the
text exposition format at GET /metrics, plus gauges read from the database at scrape time."""

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
    "por_rate_limited_total": "Requests refused by a rate limit.",
    "por_emails_total": "Emails sent or written to the outbox.",
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
        for name, help_text, labels, v in _gauges():
            if name not in seen:
                lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
                seen.add(name)
            lines.append(f"{name}{_fmt_labels(tuple(sorted(labels.items())))} {v}")
    except Exception:  # noqa: BLE001  (database unavailable: still serve process metrics)
        lines.append("# database gauges unavailable")
    return "\n".join(lines) + "\n"
