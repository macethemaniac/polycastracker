"""Add side/status/score/why_json to alerts"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260104_03"
down_revision = "20260104_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("side", sa.String(length=16), nullable=True))
    op.add_column("alerts", sa.Column("status", sa.String(length=32), nullable=True))
    op.add_column("alerts", sa.Column("score", sa.Numeric(12, 4), nullable=True))
    op.add_column("alerts", sa.Column("why_json", sa.JSON(), nullable=True))
    op.add_column(
        "alerts",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            server_onupdate=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_alerts_status", "alerts", ["status"], unique=False)
    op.create_unique_constraint("uq_alerts_market_side_event", "alerts", ["market_id", "side", "event_type"])


def downgrade() -> None:
    op.drop_constraint("uq_alerts_market_side_event", "alerts", type_="unique")
    op.drop_index("ix_alerts_status", table_name="alerts")
    op.drop_column("alerts", "updated_at")
    op.drop_column("alerts", "why_json")
    op.drop_column("alerts", "score")
    op.drop_column("alerts", "status")
    op.drop_column("alerts", "side")
