"""Source apps: an API key may be allowed to review as a named reviewer account.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_apps") as batch:
        batch.add_column(sa.Column("review_as_user_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source_apps") as batch:
        batch.drop_column("review_as_user_id")
