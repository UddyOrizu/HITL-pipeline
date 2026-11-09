"""Shared plumbing for connectors: HTTP clients, decrypted configuration and a Proof of Review API client per
source app."""

from __future__ import annotations

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
    return httpx.Client(base_url=get_settings().public_base_url, headers={"Authorization": f"Bearer {token}"}, timeout=60)


class ConnectorError(RuntimeError):
    pass


def check(resp: httpx.Response) -> httpx.Response:
    if resp.status_code >= 400:
        raise ConnectorError(f"{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text[:300]}")
    return resp
