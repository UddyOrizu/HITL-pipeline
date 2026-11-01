"""Alembic migrations build exactly the schema the models describe."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine


def test_migrations_match_models(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/migrated.db"
    monkeypatch.setenv("POR_DATABASE_URL", url)
    from services.config import get_settings

    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    from db.models import TENANT_TABLES, Base

    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
    # The Postgres RLS migration covers every tenant table.
    import importlib

    rls = importlib.import_module("db.migrations.versions.0002_postgres_controls")
    assert set(TENANT_TABLES) == set(rls.TENANT_TABLES)
    get_settings.cache_clear()
