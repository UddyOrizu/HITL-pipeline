"""Run callbacks after a session commits (e.g. deliver a webhook only once its row is saved)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger("por.hooks")


def on_commit(session: Session, fn: Callable[[], None]) -> None:
    session.info.setdefault("after_commit", []).append(fn)


@event.listens_for(Session, "after_commit")
def _run_after_commit(session: Session) -> None:
    callbacks = session.info.pop("after_commit", [])
    for fn in callbacks:
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("after-commit callback failed")


@event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    session.info.pop("after_commit", None)
