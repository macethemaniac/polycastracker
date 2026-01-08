"""Extend external_id column to support conditionId"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260107_06"
down_revision = "20260107_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend external_id from varchar(64) to varchar(128) to support conditionId
    op.alter_column(
        "markets",
        "external_id",
        type_=sa.String(length=128),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "markets",
        "external_id",
        type_=sa.String(length=64),
        existing_type=sa.String(length=128),
        existing_nullable=False,
    )
