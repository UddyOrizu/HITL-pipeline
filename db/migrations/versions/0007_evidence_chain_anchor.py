"""Evidence packs record the audit chain head they were built against (tail-truncation anchor).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("evidence_packs") as b:
        b.add_column(sa.Column("audit_head_seq", sa.Integer(), nullable=True))
        b.add_column(sa.Column("audit_head_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evidence_packs") as b:
        b.drop_column("audit_head_hash")
        b.drop_column("audit_head_seq")
