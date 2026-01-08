"""Add backtest_results table"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260104_04"
down_revision = "20260104_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_results",
        sa.Column("alert_id", sa.Integer(), sa.ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("side", sa.String(length=16), nullable=True),
        sa.Column("score", sa.Numeric(12, 4), nullable=True),
        sa.Column("alert_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_t0", sa.Numeric(24, 12), nullable=True),
        sa.Column("price_15m", sa.Numeric(24, 12), nullable=True),
        sa.Column("price_1h", sa.Numeric(24, 12), nullable=True),
        sa.Column("price_4h", sa.Numeric(24, 12), nullable=True),
        sa.Column("delta_15m", sa.Numeric(24, 12), nullable=True),
        sa.Column("delta_1h", sa.Numeric(24, 12), nullable=True),
        sa.Column("delta_4h", sa.Numeric(24, 12), nullable=True),
    )
    op.create_index("ix_backtest_results_alert", "backtest_results", ["alert_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_backtest_results_alert", table_name="backtest_results")
    op.drop_table("backtest_results")
