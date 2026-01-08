"""Initial schema for polymarket-watch"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260104_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "healthchecks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "markets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="active"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            server_onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("external_id", name="uq_markets_external_id"),
    )
    op.create_index("ix_markets_status", "markets", ["status"], unique=False)
    op.create_index("ix_markets_resolved_at", "markets", ["resolved_at"], unique=False)

    op.create_table(
        "wallet_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("wallet_address", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("risk_level", sa.String(length=50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("wallet_address", name="uq_wallet_profiles_wallet"),
    )
    op.create_index(
        "ix_wallet_profiles_created_at",
        "wallet_profiles",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "market_id",
            sa.Integer(),
            sa.ForeignKey("markets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wallet_profile_id",
            sa.Integer(),
            sa.ForeignKey("wallet_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("wallet_address", sa.String(length=128), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("shares", sa.Numeric(24, 8), nullable=False),
        sa.Column("price", sa.Numeric(24, 8), nullable=False),
        sa.Column("traded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trade_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "market_id",
            "wallet_address",
            "traded_at",
            "side",
            "shares",
            "price",
            name="uq_trades_dedupe",
        ),
    )
    op.create_index("ix_trades_market_time", "trades", ["market_id", "traded_at"], unique=False)
    op.create_index(
        "ix_trades_wallet_time",
        "trades",
        ["wallet_profile_id", "traded_at"],
        unique=False,
    )
    op.create_index("ix_trades_traded_at", "trades", ["traded_at"], unique=False)
    op.create_index("uq_trades_trade_hash", "trades", ["trade_hash"], unique=True)

    op.create_table(
        "signal_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "market_id",
            sa.Integer(),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "wallet_profile_id",
            sa.Integer(),
            sa.ForeignKey("wallet_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_signal_events_market_created",
        "signal_events",
        ["market_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_signal_events_wallet_created",
        "signal_events",
        ["wallet_profile_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wallet_profile_id",
            sa.Integer(),
            sa.ForeignKey("wallet_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "market_id",
            sa.Integer(),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_alerts_wallet_market_type",
        "alerts",
        ["wallet_profile_id", "market_id", "event_type"],
        unique=False,
    )
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"], unique=False)

    op.create_table(
        "app_state",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            server_onupdate=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("app_state")
    op.drop_index("ix_alerts_created_at", table_name="alerts")
    op.drop_index("ix_alerts_wallet_market_type", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("ix_signal_events_wallet_created", table_name="signal_events")
    op.drop_index("ix_signal_events_market_created", table_name="signal_events")
    op.drop_table("signal_events")
    op.drop_index("uq_trades_trade_hash", table_name="trades")
    op.drop_index("ix_trades_traded_at", table_name="trades")
    op.drop_index("ix_trades_wallet_time", table_name="trades")
    op.drop_index("ix_trades_market_time", table_name="trades")
    op.drop_table("trades")
    op.drop_index("ix_wallet_profiles_created_at", table_name="wallet_profiles")
    op.drop_table("wallet_profiles")
    op.drop_index("ix_markets_resolved_at", table_name="markets")
    op.drop_index("ix_markets_status", table_name="markets")
    op.drop_table("markets")
    op.drop_table("healthchecks")
