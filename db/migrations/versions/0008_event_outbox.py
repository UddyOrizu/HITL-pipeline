"""Transactional outbox for domain events published to Redis Streams.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_outbox",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("stream", sa.String(length=64), nullable=False),
        sa.Column("fields", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_event_outbox_created_at", "event_outbox", ["created_at"])
    op.create_index("ix_event_outbox_published_at", "event_outbox", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_event_outbox_published_at", table_name="event_outbox")
    op.drop_index("ix_event_outbox_created_at", table_name="event_outbox")
    op.drop_table("event_outbox")
