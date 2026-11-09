"""Operations: tenant management, sessions, password resets, model usage, retention, key rotation grace.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# New tables with a tenant_id column: they get the same row-level security policy as 0002.
RLS_TABLES = ["model_calls", "revoked_tokens", "password_reset_tokens"]
SETTING = "current_setting('app.tenant_id', true)"


def upgrade() -> None:
    with op.batch_alter_table("tenants") as b:
        b.add_column(sa.Column("active", sa.Boolean(), server_default="1", nullable=False))
    with op.batch_alter_table("teams") as b:
        b.add_column(sa.Column("hourly_rate", sa.Float(), nullable=True))
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("sessions_revoked_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("is_platform_admin", sa.Boolean(), server_default="0", nullable=False))
        b.add_column(sa.Column("email_notifications", sa.Boolean(), server_default="1", nullable=False))
        b.add_column(sa.Column("hourly_rate", sa.Float(), nullable=True))
        b.add_column(sa.Column("erased_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("source_apps") as b:
        b.add_column(sa.Column("previous_key_prefix", sa.String(length=16), nullable=True))
        b.add_column(sa.Column("previous_key_hash", sa.String(length=128), nullable=True))
        b.add_column(sa.Column("previous_key_expires_at", sa.DateTime(), nullable=True))
        b.create_index("ix_source_apps_previous_key_prefix", ["previous_key_prefix"])
    with op.batch_alter_table("submissions") as b:
        b.add_column(sa.Column("processing_attempts", sa.Integer(), server_default="0", nullable=False))
        b.add_column(sa.Column("retry_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("purged_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("connector_configs") as b:
        b.add_column(sa.Column("last_run_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("last_error", sa.Text(), nullable=True))

    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("submission_id", sa.String(length=36), nullable=True),
        sa.Column("agent", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_model_calls_submission_id", "model_calls", ["submission_id"])
    op.create_index("ix_model_calls_tenant_id", "model_calls", ["tenant_id"])
    op.create_index("ix_model_calls_tenant_time", "model_calls", ["tenant_id", "created_at"])

    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_revoked_tokens_user_id", "revoked_tokens", ["user_id"])
    op.create_index("ix_revoked_tokens_expires_at", "revoked_tokens", ["expires_at"])
    op.create_index("ix_revoked_tokens_tenant_id", "revoked_tokens", ["tenant_id"])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index("ix_password_reset_tokens_tenant_id", "password_reset_tokens", ["tenant_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in RLS_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({SETTING} = '*' OR tenant_id = {SETTING}) "
                       f"WITH CHECK ({SETTING} = '*' OR tenant_id = {SETTING})")


def downgrade() -> None:
    op.drop_table("password_reset_tokens")
    op.drop_table("revoked_tokens")
    op.drop_table("model_calls")
    with op.batch_alter_table("connector_configs") as b:
        b.drop_column("last_error")
        b.drop_column("last_run_at")
    with op.batch_alter_table("submissions") as b:
        b.drop_column("purged_at")
        b.drop_column("retry_at")
        b.drop_column("processing_attempts")
    with op.batch_alter_table("source_apps") as b:
        b.drop_index("ix_source_apps_previous_key_prefix")
        b.drop_column("previous_key_expires_at")
        b.drop_column("previous_key_hash")
        b.drop_column("previous_key_prefix")
    with op.batch_alter_table("users") as b:
        for col in ("erased_at", "hourly_rate", "email_notifications", "is_platform_admin", "sessions_revoked_at"):
            b.drop_column(col)
    with op.batch_alter_table("teams") as b:
        b.drop_column("hourly_rate")
    with op.batch_alter_table("tenants") as b:
        b.drop_column("active")
