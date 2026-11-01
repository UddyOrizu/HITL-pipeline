"""Connectors: adapters on top of the public API. register() wires event-driven connectors to domain events."""

from __future__ import annotations

_registered = False


def register() -> None:
    global _registered
    if _registered:
        return
    from connectors import jira, teams
    from services import jobs

    jobs.subscribe("task.assigned", teams.on_task_assigned)
    jobs.subscribe("submission.escalated", teams.on_escalated)
    jobs.subscribe("submission.deferred", teams.on_deferred)
    jobs.subscribe("submission.decided", jira.on_decided)
    _registered = True
