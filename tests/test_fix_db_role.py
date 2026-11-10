"""The apps' database role: not a superuser, so Postgres row-level security applies to it."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from scripts.db_app_role import ensure_app_role
from tests.conftest import PG_URL, make_tenant, submit_text


def test_role_name_is_validated():
    with pytest.raises(SystemExit):
        ensure_app_role("sqlite://", "por_app; DROP TABLE users", "x")


def test_nothing_to_do_on_sqlite(env):
    from services.config import get_settings

    assert ensure_app_role(get_settings().database_url, "por_app", "secret") is False


@pytest.mark.skipif(not PG_URL, reason="needs POR_TEST_DATABASE_URL (real Postgres)")
def test_app_role_is_held_to_row_level_security(client):
    a = make_tenant()
    b = make_tenant()
    submit_text(client, a, external_ref="tenant-a")
    submit_text(client, b, external_ref="tenant-b")

    assert ensure_app_role(PG_URL, "por_rls_test", "rls-test-password") is True
    url = make_url(PG_URL).set(username="por_rls_test", password="rls-test-password")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
            assert row == (False, False)
            conn.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": a["tenant_id"]})
            refs = set(conn.execute(text("SELECT external_ref FROM submissions")).scalars())
            assert refs == {"tenant-a"}
    finally:
        engine.dispose()
