"""Postgres controls: row-level security, append-only audit trigger, Timescale hypertable.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-26
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table with a tenant_id column. The app sets app.tenant_id per session; system jobs use '*'.
TENANT_TABLES = [
    "teams", "users", "source_apps", "workflows", "submissions", "documents", "sections", "spans", "risk_flags",
    "canaries", "review_tasks", "telemetry_events", "flag_acknowledgements", "comments", "decisions",
    "assurance_scores", "audit_events", "evidence_packs", "webhook_deliveries", "connector_configs",
]
SETTING = "current_setting('app.tenant_id', true)"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({SETTING} = '*' OR tenant_id = {SETTING}) "
            f"WITH CHECK ({SETTING} = '*' OR tenant_id = {SETTING})"
        )
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON tenants USING ({SETTING} = '*' OR id = {SETTING}) "
               f"WITH CHECK ({SETTING} = '*' OR id = {SETTING})")

    op.execute("""
        CREATE OR REPLACE FUNCTION por_audit_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events "
               "FOR EACH ROW EXECUTE FUNCTION por_audit_immutable()")
    op.execute("CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
               "FOR EACH STATEMENT EXECUTE FUNCTION por_audit_immutable()")

    # TimescaleDB is optional: plain Postgres still works, telemetry just isn't a hypertable.
    op.execute("""
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS timescaledb;
            PERFORM create_hypertable('telemetry_events', 'created_at', if_not_exists => TRUE, migrate_data => TRUE);
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'TimescaleDB not available; telemetry_events stays a regular table';
        END $$;
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS por_audit_immutable()")
    for table in [*TENANT_TABLES, "tenants"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
