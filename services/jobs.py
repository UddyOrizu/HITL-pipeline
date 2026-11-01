"""Job runner and event bus.

Inline mode (dev/test) runs jobs immediately in-process. Otherwise jobs go onto a Redis Stream
consumed by `python -m services.worker`, so each service can be scaled and evolve independently.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from services.config import get_settings

log = logging.getLogger("por.jobs")

JOB_STREAM = "por:jobs"
EVENT_STREAM = "por:events"

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


def enqueue(name: str, **kwargs) -> None:
    s = get_settings()
    if s.inline_jobs or not s.redis_url:
        run(name, kwargs)
        return
    _redis().xadd(JOB_STREAM, {"name": name, "kwargs": json.dumps(kwargs)})


def run(name: str, kwargs: dict) -> None:
    _load_jobs()
    fn = _registry.get(name)
    if fn is None:
        raise KeyError(f"unknown job {name}")
    fn(**kwargs)


def subscribe(event: str, handler: Callable[[dict], None]) -> None:
    _subscribers.setdefault(event, []).append(handler)


def publish(event: str, payload: dict, session=None) -> None:
    """Publish a domain event to in-process subscribers and, when configured, the Redis event stream.

    With a session, delivery waits until that session commits, so subscribers see the committed state.
    """
    if session is not None:
        from services.hooks import on_commit

        on_commit(session, lambda: publish(event, payload))
        return
    for handler in _subscribers.get(event, []):
        try:
            handler(payload)
        except Exception:  # noqa: BLE001
            log.exception("event handler failed for %s", event)
    s = get_settings()
    if s.redis_url and not s.inline_jobs:
        try:
            _redis().xadd(EVENT_STREAM, {"event": event, "payload": json.dumps(payload, default=str)}, maxlen=100000)
        except Exception:  # noqa: BLE001
            log.exception("failed to publish %s to redis", event)


def _load_jobs() -> None:
    # Importing registers @job functions.
    import services.pipeline  # noqa: F401
    import services.webhooks  # noqa: F401
