"""Connectors: adapters on top of the public API.

register() wires event-driven connectors (Teams, Jira, Azure DevOps) to domain events. poll_one() runs a polling
connector (SharePoint folders, Jira and Azure DevOps queries) once, under a per-connector lock; the scheduler runs
each active configuration separately, so one broken or slow configuration never stops or delays the others.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from db.models import ConnectorConfig, utcnow
from db.session import session_scope

log = logging.getLogger("por.connectors")

_registered = False

# Per kind: required config keys, secret keys (stored encrypted, never returned), URL keys (checked against
# private networks), and the key that switches polling on (True: always polls).
KINDS: dict[str, dict] = {
    "sharepoint": {"required": ["aad_tenant_id", "client_id", "client_secret", "drive_id", "workflow_id"],
                   "secret": {"client_secret"}, "urls": [], "polls": True},
    "teams": {"required": ["webhook_url"], "secret": {"webhook_url"}, "urls": ["webhook_url", "app_base_url"],
              "polls": False},
    "jira": {"required": ["base_url", "email", "api_token"], "secret": {"api_token"}, "urls": ["base_url"],
             "polls": "submit_jql"},
    "azure_devops": {"required": ["organization", "project", "pat"], "secret": {"pat"}, "urls": ["base_url"],
                     "polls": "submit_wiql"},
}


def kind_of(cfg: ConnectorConfig) -> str:
    """Older configurations stored Azure DevOps as kind "jira" with flavour "azure_devops"."""
    if cfg.kind == "jira" and (cfg.config or {}).get("flavour") == "azure_devops":
        return "azure_devops"
    return cfg.kind


def register() -> None:
    global _registered
    if _registered:
        return
    from connectors import azure_devops, jira, teams
    from services import jobs, notifications

    jobs.subscribe("task.assigned", teams.on_task_assigned)
    jobs.subscribe("submission.escalated", teams.on_escalated)
    jobs.subscribe("submission.deferred", teams.on_deferred)
    jobs.subscribe("submission.decided", jira.on_decided)
    jobs.subscribe("submission.decided", azure_devops.on_decided)
    notifications.register()
    _registered = True


def polls(cfg: ConnectorConfig) -> bool:
    spec = KINDS.get(kind_of(cfg))
    if not spec or not spec["polls"]:
        return False
    return spec["polls"] is True or bool((cfg.config or {}).get(spec["polls"]))


def steps(cfg: ConnectorConfig) -> list[tuple[str, Callable]]:
    """The polling steps of a connector, each run and committed on its own: a failing decision write-back must
    not roll back the submissions the poll step just made (they would be submitted again on the next run)."""
    kind = kind_of(cfg)
    if kind == "sharepoint":
        from connectors import sharepoint

        return [("poll", sharepoint.poll_step), ("sync", sharepoint.sync_step)]
    if kind == "jira":
        from connectors import jira

        return [("poll", jira.run_once)]
    if kind == "azure_devops":
        from connectors import azure_devops

        return [("poll", azure_devops.run_once)]
    return []


def run(session, cfg: ConnectorConfig) -> dict:
    """All steps in one session (kept for callers that manage their own transaction)."""
    result: dict = {}
    for _, step in steps(cfg):
        result.update(step(session, cfg))
    return result


def lock_name(connector_id: str) -> str:
    return f"connector:{connector_id}"


LOCK_TTL = 300  # renewed while the connector runs


def poll_one(connector_id: str, force: bool = False, lock: bool = True) -> dict:
    """Run one connector's polling steps under its own lock and record the outcome on the configuration.

    Returns {"skipped": True, "busy": True} when the connector is already running here or on another replica.
    lock=False: the caller already holds the connector's lock (the scheduler does)."""
    with session_scope() as s:
        cfg = s.get(ConnectorConfig, connector_id)
        if cfg is None or not cfg.active or (not force and not polls(cfg)):
            return {"skipped": True}
        tenant_id = cfg.tenant_id
    if not lock:
        return _run_steps(connector_id, tenant_id)
    from services import scheduler

    release = scheduler.acquire(lock_name(connector_id), LOCK_TTL, renew=True)
    if release is None:
        return {"skipped": True, "busy": True}
    try:
        return _run_steps(connector_id, tenant_id)
    finally:
        release()


def _run_steps(connector_id: str, tenant_id: str) -> dict:
    result: dict = {}
    errors: list[str] = []
    with session_scope(tenant_id) as s:
        cfg = s.get(ConnectorConfig, connector_id)
        names = steps(cfg) if cfg is not None else []
    for name, step in names:
        try:
            with session_scope(tenant_id) as s:
                out = step(s, s.get(ConnectorConfig, connector_id)) or {}
        except Exception as exc:  # noqa: BLE001
            log.exception("connector %s: %s step failed", connector_id, name)
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        errors += out.pop("errors", [])  # items that failed on their own (the others went through)
        result.update(out)
    with session_scope(tenant_id) as s:
        cfg = s.get(ConnectorConfig, connector_id)
        if cfg is None:  # deleted while running
            return result
        cfg.last_run_at = utcnow()
        cfg.last_error = "; ".join(errors)[:2000] or None
    if errors:
        result["error"] = "; ".join(errors)[:500]
    return result
