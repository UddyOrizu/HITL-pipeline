"""Submission and review channels: the built-in web portal source app, and where each decision was made.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_apps") as batch:
        batch.add_column(sa.Column("kind", sa.String(20), server_default="api", nullable=False))
    with op.batch_alter_table("decisions") as batch:
        batch.add_column(sa.Column("channel", sa.String(10), server_default="api", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("decisions") as batch:
        batch.drop_column("channel")
    with op.batch_alter_table("source_apps") as batch:
        batch.drop_column("kind")
