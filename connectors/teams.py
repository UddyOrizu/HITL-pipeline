"""Microsoft Teams connector: reviewer nudges and escalation notices as Adaptive Cards.

Approve-from-queue links open the review workspace; the decision itself always happens there, where telemetry runs.

Config keys: webhook_url (secret: Teams Workflows / incoming webhook URL), app_base_url (reviewer web app),
             notify ("assigned,escalated,deferred,sla_reminder" by default).

sla_reminder: one nudge per review task once it has used 75% of its stage's SLA (sla_hours) without a decision.
The scheduler checks every few minutes; tasks already nudged are remembered in the connector's state.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from connectors.base import check, checked_url, config_of, http, save_state
from db.models import ConnectorConfig, ReviewTask, Submission, User, utcnow
from db.session import session_scope
from services.config import get_settings

log = logging.getLogger("por.connectors.teams")

DEFAULT_NOTIFY = "assigned,escalated,deferred,sla_reminder"
NUDGE_AT = 0.75  # share of the SLA used before the reminder goes out
MAX_NUDGED = 5000


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
    return what in [w.strip() for w in (cfg.config or {}).get("notify", DEFAULT_NOTIFY).split(",")]


def on_task_assigned(event: dict) -> None:
    with session_scope(event["tenant_id"]) as s:
        reviewer = s.get(User, event["reviewer_id"])
        sub = s.get(Submission, event["submission_id"])
        for cfg in _configs(s, event["tenant_id"], "teams"):
            if not _wants(cfg, "assigned") or (sub and sub.source_app_id != cfg.source_app_id):
                continue
            base = (cfg.config or {}).get("app_base_url") or get_settings().app_url
            title = (sub.meta or {}).get("title") if sub else None
            # The reviewer may have been erased or removed since the event was published.
            who = f"{reviewer.name or reviewer.email}, you have" if reviewer else "There is"
            post(cfg, _card(
                "New item in your review queue",
                [f"{who} a {event.get('stage', 'review')} task"
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


def _nudge_due(task: ReviewTask, now) -> bool:
    if task.due_at is None or task.assigned_at is None or task.due_at <= task.assigned_at:
        return False
    return now >= task.assigned_at + (task.due_at - task.assigned_at) * NUDGE_AT


def send_sla_nudges() -> int:
    """One reminder card per open task past 75% of its SLA. Returns the number of cards sent."""
    now = utcnow()
    sent = 0
    with session_scope() as s:
        tenants = {c.tenant_id for c in s.scalars(select(ConnectorConfig).where(
            ConnectorConfig.kind == "teams", ConnectorConfig.active.is_(True))).all()}
    for tenant_id in tenants:
        with session_scope(tenant_id) as s:
            configs = [c for c in _configs(s, tenant_id, "teams") if _wants(c, "sla_reminder")]
            if not configs:
                continue
            tasks = [t for t in s.scalars(select(ReviewTask).where(
                ReviewTask.tenant_id == tenant_id, ReviewTask.status == "assigned", ReviewTask.due_at.is_not(None),
                ReviewTask.sla_escalated.is_(False))).all() if _nudge_due(t, now)]
            for cfg in configs:
                nudged = list((cfg.state or {}).get("nudged", []))
                done = set(nudged)
                base = (cfg.config or {}).get("app_base_url") or get_settings().app_url
                for task in tasks:
                    if task.id in done:
                        continue
                    sub = s.get(Submission, task.submission_id)
                    if sub is None or sub.source_app_id != cfg.source_app_id:
                        continue
                    reviewer = s.get(User, task.reviewer_id)
                    title = (sub.meta or {}).get("title")
                    hours_left = max(0.0, (task.due_at - now).total_seconds() / 3600)
                    try:
                        post(cfg, _card(
                            "Review due soon",
                            [(f"{reviewer.name or reviewer.email}, your" if reviewer else "A")
                             + f" {task.stage_name} task" + (f" ({title})" if title else "")
                             + f" is due in {hours_left:.1f} hours."],
                            f"{base}/review/{task.id}", "Open in review workspace",
                        ))
                    except Exception:  # noqa: BLE001  (try again next pass)
                        log.exception("teams SLA reminder for task %s failed", task.id)
                        continue
                    nudged.append(task.id)
                    done.add(task.id)
                    sent += 1
                if len(nudged) != len((cfg.state or {}).get("nudged", [])):
                    save_state(cfg, nudged=nudged[-MAX_NUDGED:])
    return sent
