"""Engine and session factory.

On Postgres every session sets `app.tenant_id`, which row-level security policies read.
System jobs that work across tenants use the wildcard '*'.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from services.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        if ":///" in url and not url.endswith(":memory:"):
            Path(url.split(":///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine(url: str | None = None) -> Engine:
    """Used by tests to point at a fresh database."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = _make_engine(url or get_settings().database_url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def is_postgres(session: Session) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def set_tenant(session: Session, tenant_id: str) -> None:
    """Scope a session to one tenant for row-level security (no-op outside Postgres)."""
    session.info["tenant_id"] = tenant_id
    if is_postgres(session):
        session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": tenant_id})


def new_session(tenant_id: str = "*") -> Session:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    set_tenant(session, tenant_id)
    return session


@contextmanager
def session_scope(tenant_id: str = "*") -> Iterator[Session]:
    session = new_session(tenant_id)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
