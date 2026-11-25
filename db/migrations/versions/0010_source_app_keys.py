"""Source apps: several API keys can be live at once, each named and revoked on its own.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = ["source_app_keys"]
SETTING = "current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.create_table(
        "source_app_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_app_id", sa.String(length=36), sa.ForeignKey("source_apps.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_source_app_keys_source_app_id", "source_app_keys", ["source_app_id"])
    op.create_index("ix_source_app_keys_key_prefix", "source_app_keys", ["key_prefix"])
    op.create_index("ix_source_app_keys_tenant_id", "source_app_keys", ["tenant_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in RLS_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({SETTING} = '*' OR tenant_id = {SETTING}) "
                       f"WITH CHECK ({SETTING} = '*' OR tenant_id = {SETTING})")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in RLS_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_index("ix_source_app_keys_tenant_id", table_name="source_app_keys")
    op.drop_index("ix_source_app_keys_key_prefix", table_name="source_app_keys")
    op.drop_index("ix_source_app_keys_source_app_id", table_name="source_app_keys")
    op.drop_table("source_app_keys")
