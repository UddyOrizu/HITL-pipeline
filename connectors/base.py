"""Shared plumbing for connectors: HTTP clients, decrypted configuration and a Proof of Review API client per
source app."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from db.models import ConnectorConfig
from services import netguard, secretbox
from services.config import get_settings
from services.security import issue_token

_transport: httpx.BaseTransport | None = None
_por_client_factory: Callable[[str, str], httpx.Client] | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Tests inject an httpx.MockTransport for external services (Graph, Teams, Jira, Azure DevOps)."""
    global _transport
    _transport = transport


def set_por_client_factory(factory: Callable[[str, str], httpx.Client] | None) -> None:
    """Tests inject a factory that returns a TestClient bound to the in-process API."""
    global _por_client_factory
    _por_client_factory = factory


def config_of(cfg: ConnectorConfig) -> dict:
    """The configuration with secrets decrypted, for use in memory only."""
    return secretbox.reveal(cfg.config)


def http(**kwargs) -> httpx.Client:
    return httpx.Client(transport=_transport, timeout=30, **kwargs)


def checked_url(url: str) -> str:
    """Refuse private-network targets (resolution is skipped when tests inject a transport)."""
    netguard.check_url(url, resolve=_transport is None)
    return url


def por_client(source_app_id: str, tenant_id: str) -> httpx.Client:
    """Connectors are adapters on top of the public API: they call it as their source app with a short-lived token."""
    token = issue_token(source_app_id, tenant_id, "app", ttl_minutes=10)
    if _por_client_factory is not None:
        client = _por_client_factory(source_app_id, tenant_id)
        client.headers["Authorization"] = f"Bearer {token}"
        return client
    # settings.api_url: the internal address when one is set (http://api:8000 in Compose), not the public URL.
    return httpx.Client(base_url=get_settings().api_url, headers={"Authorization": f"Bearer {token}"}, timeout=60)


class ConnectorError(RuntimeError):
    pass


def check(resp: httpx.Response) -> httpx.Response:
    if resp.status_code >= 400:
        raise ConnectorError(f"{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text[:300]}")
    return resp


def content_hash(*parts) -> str:
    """A short fingerprint of the content a connector submits, to tell a revised ticket from one that only had
    its status or comments changed (our own write-back changes Jira's `updated` and Azure DevOps' System.Rev)."""
    import hashlib

    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]


def save_state(cfg: ConnectorConfig, **keys) -> None:
    """Write some top-level keys of the connector's state, leaving the others as they are now in the database.

    The polling step and the decision event handlers both keep state on the configuration; re-reading the row
    (locked on Postgres) just before writing keeps one from overwriting what the other recorded meanwhile."""
    from sqlalchemy.orm import object_session

    session = object_session(cfg)
    if session is not None and cfg.id is not None:
        session.flush()
        session.refresh(cfg, ["state"], with_for_update=True)
    state = dict(cfg.state or {})
    state.update(keys)
    cfg.state = state


MAX_WRITEBACKS = 1000


def writeback_done(cfg: ConnectorConfig, key: str) -> dict:
    """Steps of a decision write-back already done (e.g. {"comment": true}), so a retried event skips them."""
    return dict(((cfg.state or {}).get("writeback") or {}).get(key) or {})


def mark_writeback(cfg: ConnectorConfig, key: str, step: str, value=True) -> None:
    """Record one finished write-back step in its own transaction, so it survives a later step failing (the event
    handler's own session is rolled back then, and the retry must not comment or attach twice)."""
    from db.session import session_scope

    with session_scope(cfg.tenant_id) as s:
        row = s.get(ConnectorConfig, cfg.id, with_for_update=True)
        if row is None:
            return
        state = dict(row.state or {})
        marks = dict(state.get("writeback") or {})
        entry = dict(marks.pop(key, None) or {})
        entry[step] = value
        marks[key] = entry  # most recent last
        while len(marks) > MAX_WRITEBACKS:
            marks.pop(next(iter(marks)))
        state["writeback"] = marks
        row.state = state
    # Keep the caller's copy in step, without marking it dirty in the caller's session.
    from sqlalchemy.orm.attributes import set_committed_value

    set_committed_value(cfg, "state", {**(cfg.state or {}), "writeback": marks})
