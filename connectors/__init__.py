"""Connectors: adapters on top of the public API.

register() wires event-driven connectors (Teams, Jira, Azure DevOps) to domain events. poll_one() runs a polling
connector (SharePoint folders, Jira and Azure DevOps queries) once; the scheduler calls it for each active
configuration separately, so one broken configuration never stops the others.
"""

from __future__ import annotations

import logging

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


def run(session, cfg: ConnectorConfig) -> dict:
    kind = kind_of(cfg)
    if kind == "sharepoint":
        from connectors import sharepoint

        return sharepoint.run_once(session, cfg)
    if kind == "jira":
        from connectors import jira

        return jira.run_once(session, cfg)
    if kind == "azure_devops":
        from connectors import azure_devops

        return azure_devops.run_once(session, cfg)
    return {}


def poll_one(connector_id: str, force: bool = False) -> dict:
    """Run one connector's polling step and record the outcome on the configuration."""
    with session_scope() as s:
        cfg = s.get(ConnectorConfig, connector_id)
        if cfg is None or not cfg.active or (not force and not polls(cfg)):
            return {"skipped": True}
        tenant_id = cfg.tenant_id
    try:
        with session_scope(tenant_id) as s:
            cfg = s.get(ConnectorConfig, connector_id)
            result = run(s, cfg)
            cfg.last_run_at = utcnow()
            cfg.last_error = None
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("connector %s failed", connector_id)
        with session_scope(tenant_id) as s:
            cfg = s.get(ConnectorConfig, connector_id)
            cfg.last_run_at = utcnow()
            cfg.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        return {"error": f"{type(exc).__name__}: {exc}"[:500]}
