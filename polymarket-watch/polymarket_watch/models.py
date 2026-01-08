from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Numeric, String, Text, func, UniqueConstraint

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Healthcheck(Base):
    __tablename__ = "healthchecks"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (
        Index("ix_markets_status", "status"),
        Index("ix_markets_resolved_at", "resolved_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    trades: Mapped[list["Trade"]] = relationship(back_populates="market", cascade="all, delete-orphan")


class WalletProfile(Base):
    __tablename__ = "wallet_profiles"
    __table_args__ = (Index("ix_wallet_profiles_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_address: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    risk_level: Mapped[str | None] = mapped_column(String(50))
    is_watched: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    notes: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    trades: Mapped[list["Trade"]] = relationship(back_populates="wallet_profile")


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_market_time", "market_id", "traded_at"),
        Index("ix_trades_wallet_time", "wallet_profile_id", "traded_at"),
        Index("ix_trades_traded_at", "traded_at"),
        Index("uq_trades_trade_hash", "trade_hash", unique=True),
        Index(
            "uq_trades_dedupe",
            "market_id",
            "wallet_address",
            "traded_at",
            "side",
            "shares",
            "price",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), nullable=False)
    wallet_profile_id: Mapped[int | None] = mapped_column(ForeignKey("wallet_profiles.id"))
    wallet_address: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    shares: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    traded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trade_hash: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    market: Mapped[Market] = relationship(back_populates="trades")
    wallet_profile: Mapped[WalletProfile | None] = relationship(back_populates="trades")


class SignalEvent(Base):
    __tablename__ = "signal_events"
    __table_args__ = (
        Index("ix_signal_events_market_created", "market_id", "created_at"),
        Index("ix_signal_events_wallet_created", "wallet_profile_id", "created_at"),
        Index("ix_signal_events_wallet_address_created", "wallet_address", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    wallet_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("wallet_profiles.id", ondelete="SET NULL")
    )
    wallet_address: Mapped[str | None] = mapped_column(String(128))
    side: Mapped[str | None] = mapped_column(String(16))
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str | None] = mapped_column(String(32))
    score: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_wallet_market_type", "wallet_profile_id", "market_id", "event_type"),
        Index("ix_alerts_created_at", "created_at"),
        Index("ix_alerts_status", "status"),
        UniqueConstraint("market_id", "side", "event_type", name="uq_alerts_market_side_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("wallet_profiles.id", ondelete="SET NULL")
    )
    market_id: Mapped[int | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    side: Mapped[str | None] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str | None] = mapped_column(Text())
    status: Mapped[str | None] = mapped_column(String(32))
    score: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    why_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BacktestResult(Base):
    __tablename__ = "backtest_results"
    __table_args__ = (Index("ix_backtest_results_alert", "alert_id"),)

    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True)
    market_id: Mapped[int | None] = mapped_column(ForeignKey("markets.id", ondelete="SET NULL"))
    side: Mapped[str | None] = mapped_column(String(16))
    score: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    alert_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_t0: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    price_15m: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    price_1h: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    price_4h: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    delta_15m: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    delta_1h: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    delta_4h: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))


class WalletStats(Base):
    """Tracks wallet accuracy for early positioning detection.

    A wallet is considered to have "early positioned" correctly when:
    - They took a position (buy/sell)
    - The price moved favorably within a lookback window (15m, 1h, 4h)

    Accuracy = correct_calls / total_evaluated_trades
    """
    __tablename__ = "wallet_stats"
    __table_args__ = (
        Index("ix_wallet_stats_accuracy", "accuracy_score"),
        Index("ix_wallet_stats_updated", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_address: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)

    # Trade counts
    total_trades: Mapped[int] = mapped_column(default=0, nullable=False)
    evaluated_trades: Mapped[int] = mapped_column(default=0, nullable=False)

    # Accuracy metrics (trades where price moved in their favor)
    correct_15m: Mapped[int] = mapped_column(default=0, nullable=False)
    correct_1h: Mapped[int] = mapped_column(default=0, nullable=False)
    correct_4h: Mapped[int] = mapped_column(default=0, nullable=False)

    # Aggregate accuracy score (weighted average)
    accuracy_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))

    # Average profit when correct (measures conviction quality)
    avg_delta_when_correct: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))

    # Total notional traded (filters out tiny wallets)
    total_notional: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))

    # Streak tracking (consecutive correct calls)
    current_streak: Mapped[int] = mapped_column(default=0, nullable=False)
    best_streak: Mapped[int] = mapped_column(default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
