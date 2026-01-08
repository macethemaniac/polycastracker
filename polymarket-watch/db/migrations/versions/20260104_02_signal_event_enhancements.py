"""Add signal details columns"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260104_02"
down_revision = "20260104_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signal_events", sa.Column("wallet_address", sa.String(length=128), nullable=True))
    op.add_column("signal_events", sa.Column("side", sa.String(length=16), nullable=True))
    op.add_column("signal_events", sa.Column("severity", sa.String(length=32), nullable=True))
    op.add_column("signal_events", sa.Column("score", sa.Numeric(12, 4), nullable=True))
    op.add_column("signal_events", sa.Column("details_json", sa.JSON(), nullable=True))
    op.create_index(
        "ix_signal_events_wallet_address_created",
        "signal_events",
        ["wallet_address", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_signal_events_wallet_address_created", table_name="signal_events")
    op.drop_column("signal_events", "details_json")
    op.drop_column("signal_events", "score")
    op.drop_column("signal_events", "severity")
    op.drop_column("signal_events", "side")
    op.drop_column("signal_events", "wallet_address")
