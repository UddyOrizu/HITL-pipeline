"""Email notifications. Each tenant switches them on in settings.notifications; each person can opt out
(email_notifications on their account).

- assigned:  a reviewer gets a new task
- escalated: oversight leads hear about escalations (SLA breaches, disagreements, capacity, release checks)
- decided:   the person who sent a web-portal submission hears it was decided
"""

from __future__ import annotations

from sqlalchemy import select

from db.models import Submission, Tenant, User
from db.session import session_scope
from services import jobs, mailer
from services.config import get_settings

_registered = False


def _enabled(tenant: Tenant | None, kind: str) -> bool:
    return bool(tenant and tenant.active and ((tenant.settings or {}).get("notifications") or {}).get(kind))


def _title(sub: Submission | None) -> str:
    if sub is None:
        return "an item"
    return (sub.meta or {}).get("title") or sub.external_ref or f"submission {sub.id[:8]}"


def on_task_assigned(event: dict) -> None:
    with session_scope(event["tenant_id"]) as s:
        tenant = s.get(Tenant, event["tenant_id"])
        if not _enabled(tenant, "assigned"):
            return
        u = s.get(User, event["reviewer_id"])
        sub = s.get(Submission, event["submission_id"])
        if u is None or not u.active or not u.email_notifications:
            return
        app = get_settings().app_url
        mailer.send(u.email, f"New review task: {_title(sub)}",
                    f"Hello {u.name or u.email},\n\nYou have a new {event.get('stage') or 'review'} task: {_title(sub)}.\n"
                    f"Open it in the review workspace: {app}/review/{event['task_id']}\n")


def on_escalated(event: dict) -> None:
    with session_scope(event["tenant_id"]) as s:
        tenant = s.get(Tenant, event["tenant_id"])
        if not _enabled(tenant, "escalated"):
            return
        sub = s.get(Submission, event["submission_id"])
        leads = [u for u in s.scalars(select(User).where(User.tenant_id == event["tenant_id"], User.active.is_(True))).all()
                 if "oversight_lead" in (u.roles or []) and u.email_notifications]
        app = get_settings().app_url
        for u in leads:
            mailer.send(u.email, f"Escalated: {_title(sub)}",
                        f"{_title(sub)} needs oversight attention.\nReason: {event.get('reason') or 'unknown'}\n"
                        f"Held items: {app}/held\n")


def on_decided(event: dict) -> None:
    with session_scope(event["tenant_id"]) as s:
        tenant = s.get(Tenant, event["tenant_id"])
        if not _enabled(tenant, "decided"):
            return
        sub = s.get(Submission, event["submission_id"])
        submitter = (sub.meta or {}).get("submitted_by") if sub else None
        u = s.get(User, submitter) if submitter else None
        if u is None or not u.active or not u.email_notifications:
            return
        app = get_settings().app_url
        mailer.send(u.email, f"Decision on {_title(sub)}: {(sub.status or '').replace('_', ' ')}",
                    f"Your submission {_title(sub)} has been decided: {(sub.status or '').replace('_', ' ')}.\n"
                    f"See the decision and feedback: {app}/submissions\n")


def register() -> None:
    global _registered
    if _registered:
        return
    jobs.subscribe("task.assigned", on_task_assigned)
    jobs.subscribe("submission.escalated", on_escalated)
    jobs.subscribe("submission.decided", on_decided)
    _registered = True
