"""Add wallet_stats table for early positioning detection"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260107_05"
down_revision = "20260104_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wallet_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("wallet_address", sa.String(length=128), unique=True, nullable=False),
        # Trade counts
        sa.Column("total_trades", sa.Integer(), default=0, nullable=False),
        sa.Column("evaluated_trades", sa.Integer(), default=0, nullable=False),
        # Accuracy metrics
        sa.Column("correct_15m", sa.Integer(), default=0, nullable=False),
        sa.Column("correct_1h", sa.Integer(), default=0, nullable=False),
        sa.Column("correct_4h", sa.Integer(), default=0, nullable=False),
        # Aggregate scores
        sa.Column("accuracy_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("avg_delta_when_correct", sa.Numeric(12, 8), nullable=True),
        sa.Column("total_notional", sa.Numeric(24, 8), nullable=True),
        # Streak tracking
        sa.Column("current_streak", sa.Integer(), default=0, nullable=False),
        sa.Column("best_streak", sa.Integer(), default=0, nullable=False),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index("ix_wallet_stats_accuracy", "wallet_stats", ["accuracy_score"], unique=False)
    op.create_index("ix_wallet_stats_updated", "wallet_stats", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_wallet_stats_updated", table_name="wallet_stats")
    op.drop_index("ix_wallet_stats_accuracy", table_name="wallet_stats")
    op.drop_table("wallet_stats")
