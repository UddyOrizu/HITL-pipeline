"""Microsoft Teams connector: reviewer nudges and escalation notices as Adaptive Cards.

Approve-from-queue links open the review workspace; the decision itself always happens there, where telemetry runs.

Config keys: webhook_url (secret: Teams Workflows / incoming webhook URL), app_base_url (reviewer web app),
             notify ("assigned,escalated,deferred" by default).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from connectors.base import check, checked_url, config_of, http
from db.models import ConnectorConfig, Submission, User
from db.session import session_scope
from services.config import get_settings

log = logging.getLogger("por.connectors.teams")


def _card(title: str, lines: list[str], url: str | None, button: str) -> dict:
    body = [{"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": title, "wrap": True}]
    body += [{"type": "TextBlock", "text": line, "wrap": True, "spacing": "Small"} for line in lines]
    actions = [{"type": "Action.OpenUrl", "title": button, "url": url}] if url else []
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                        "version": "1.4", "body": body, "actions": actions},
        }],
    }


def post(cfg: ConnectorConfig, payload: dict) -> None:
    with http() as client:
        check(client.post(checked_url(config_of(cfg)["webhook_url"]), json=payload))


def _configs(session, tenant_id: str, kind: str) -> list[ConnectorConfig]:
    return list(session.scalars(select(ConnectorConfig).where(
        ConnectorConfig.tenant_id == tenant_id, ConnectorConfig.kind == kind, ConnectorConfig.active.is_(True))).all())


def _wants(cfg: ConnectorConfig, what: str) -> bool:
    return what in (cfg.config or {}).get("notify", "assigned,escalated,deferred").split(",")


def on_task_assigned(event: dict) -> None:
    with session_scope(event["tenant_id"]) as s:
        reviewer = s.get(User, event["reviewer_id"])
        sub = s.get(Submission, event["submission_id"])
        for cfg in _configs(s, event["tenant_id"], "teams"):
            if not _wants(cfg, "assigned") or (sub and sub.source_app_id != cfg.source_app_id):
                continue
            base = (cfg.config or {}).get("app_base_url") or get_settings().app_url
            title = (sub.meta or {}).get("title") if sub else None
            post(cfg, _card(
                "New item in your review queue",
                [f"{reviewer.name or reviewer.email}, you have a {event.get('stage', 'review')} task"
                 + (f": {title}" if title else "."), f"Risk tier: {sub.risk_tier if sub else '-'}"],
                f"{base}/review/{event['task_id']}", "Open in review workspace",
            ))


def on_escalated(event: dict) -> None:
    with session_scope(event["tenant_id"]) as s:
        sub = s.get(Submission, event["submission_id"])
        for cfg in _configs(s, event["tenant_id"], "teams"):
            if not _wants(cfg, "escalated") or (sub and sub.source_app_id != cfg.source_app_id):
                continue
            base = (cfg.config or {}).get("app_base_url") or get_settings().app_url
            post(cfg, _card(
                "Review escalated to oversight",
                [f"Submission {event['submission_id'][:8]} needs attention.", f"Reason: {event.get('reason', 'unknown')}"],
                f"{base}/oversight", "Open oversight dashboard",
            ))


def on_deferred(event: dict) -> None:
    with session_scope(event["tenant_id"]) as s:
        sub = s.get(Submission, event["submission_id"])
        for cfg in _configs(s, event["tenant_id"], "teams"):
            if not _wants(cfg, "deferred") or (sub and sub.source_app_id != cfg.source_app_id):
                continue
            post(cfg, _card("Review deferred", [f"Submission {event['submission_id'][:8]} was deferred: "
                                                 f"{event.get('reason', 'capacity')}."], None, ""))
