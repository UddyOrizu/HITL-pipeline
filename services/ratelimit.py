"""Fixed-window rate limits. Counters live in Redis when POR_REDIS_URL is set (shared by every API replica),
otherwise in process memory."""

from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

from services.config import get_settings

_lock = threading.Lock()
_counts: dict[str, tuple[int, int]] = {}  # key -> (window, count)


def reset() -> None:
    with _lock:
        _counts.clear()


def client_ip(request: Request) -> str:
    """The client address. Behind a trusted proxy (POR_TRUST_FORWARDED_FOR) it is the rightmost X-Forwarded-For
    entry: the address the proxy itself saw and appended. Entries to its left come from the client and can be forged."""
    if get_settings().trust_forwarded_for:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            hops = [h.strip() for h in fwd.split(",") if h.strip()]
            if hops:
                return hops[-1]
    return request.client.host if request.client else "unknown"


def _local_hit(key: str, window: int) -> int:
    with _lock:
        w, n = _counts.get(key, (window, 0))
        if w != window:
            n = 0
        n += 1
        _counts[key] = (window, n)
        if len(_counts) > 50000:  # bound memory: drop stale windows
            for k in [k for k, (w2, _) in _counts.items() if w2 != window]:
                del _counts[k]
        return n


def hit(key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
    """Count one request. Returns (allowed, seconds until the window resets)."""
    now = int(time.time())
    window = now // window_seconds
    retry = window_seconds - (now % window_seconds)
    if get_settings().redis_url:
        try:
            from services.jobs import _redis

            r = _redis()
            k = f"por:rl:{key}:{window}"
            n = r.incr(k)
            if n == 1:
                r.expire(k, window_seconds + 1)
            return n <= limit, retry
        except Exception:  # noqa: BLE001  (Redis down: count locally rather than fail open)
            pass
    return _local_hit(key, window) <= limit, retry


def enforce(key: str, limit: int) -> None:
    if not get_settings().rate_limit_enabled or limit <= 0:
        return
    allowed, retry = hit(key, limit)
    if not allowed:
        from services import metrics

        metrics.inc("por_rate_limited_total", {"bucket": key.split(":", 1)[0]})
        raise HTTPException(429, "too many requests; try again shortly", headers={"Retry-After": str(retry)})


def auth_limit(bucket: str):
    """Dependency: per-IP limit for sign-in and other unauthenticated endpoints."""

    def dep(request: Request) -> None:
        enforce(f"{bucket}:{client_ip(request)}", get_settings().rate_limit_auth_per_minute)

    return dep
