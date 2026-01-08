"""Wallet accuracy scoring service.

Evaluates wallet trades and tracks how often they "call it right" -
i.e., position before price moves in their favor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymarket_watch.models import Trade, WalletStats

logger = logging.getLogger(__name__)

# Minimum price move to count as "correct" (5% favorable move)
MIN_FAVORABLE_DELTA = Decimal("0.05")

# Minimum trades before a wallet is considered for early positioning signals
MIN_EVALUATED_TRADES = 5

# Minimum accuracy to be flagged as a "smart wallet"
MIN_ACCURACY_THRESHOLD = Decimal("0.60")

# Minimum notional to filter out dust trades
MIN_NOTIONAL_THRESHOLD = Decimal("100")

# Weights for different time horizons when computing aggregate accuracy
ACCURACY_WEIGHTS = {
    "15m": Decimal("0.2"),
    "1h": Decimal("0.3"),
    "4h": Decimal("0.5"),
}


@dataclass
class TradeOutcome:
    """Result of evaluating a single trade."""
    trade_id: int
    wallet_address: str
    side: str
    price_at_trade: Decimal
    price_15m: Decimal | None
    price_1h: Decimal | None
    price_4h: Decimal | None
    correct_15m: bool
    correct_1h: bool
    correct_4h: bool
    delta_15m: Decimal | None
    delta_1h: Decimal | None
    delta_4h: Decimal | None
    notional: Decimal


def is_favorable_move(side: str, entry_price: Decimal, later_price: Decimal) -> bool:
    """Check if price moved favorably for the position.

    For buys: price going UP is favorable
    For sells: price going DOWN is favorable
    """
    if later_price is None:
        return False

    delta = later_price - entry_price

    if side.lower() == "buy":
        return delta >= MIN_FAVORABLE_DELTA
    else:  # sell
        return delta <= -MIN_FAVORABLE_DELTA


def calculate_delta(side: str, entry_price: Decimal, later_price: Decimal | None) -> Decimal | None:
    """Calculate the signed delta (positive = favorable)."""
    if later_price is None:
        return None

    raw_delta = later_price - entry_price

    if side.lower() == "buy":
        return raw_delta
    else:  # sell - invert so positive = favorable
        return -raw_delta


class WalletAccuracyScorer:
    """Scores wallets based on their historical trade accuracy."""

    def __init__(
        self,
        min_favorable_delta: Decimal = MIN_FAVORABLE_DELTA,
        min_evaluated_trades: int = MIN_EVALUATED_TRADES,
        min_accuracy: Decimal = MIN_ACCURACY_THRESHOLD,
        min_notional: Decimal = MIN_NOTIONAL_THRESHOLD,
    ) -> None:
        self.min_favorable_delta = min_favorable_delta
        self.min_evaluated_trades = min_evaluated_trades
        self.min_accuracy = min_accuracy
        self.min_notional = min_notional

    def get_price_at_time(
        self,
        session: Session,
        market_id: int,
        target_time: datetime,
        tolerance: timedelta = timedelta(minutes=5),
    ) -> Decimal | None:
        """Get the price closest to target_time within tolerance."""
        lower_bound = target_time - tolerance
        upper_bound = target_time + tolerance

        row = session.execute(
            select(Trade.price, Trade.traded_at)
            .where(
                Trade.market_id == market_id,
                Trade.traded_at >= lower_bound,
                Trade.traded_at <= upper_bound,
            )
            .order_by(func.abs(func.extract("epoch", Trade.traded_at - target_time)))
            .limit(1)
        ).first()

        return Decimal(row[0]) if row else None

    def evaluate_trade(
        self,
        session: Session,
        trade: Trade,
    ) -> TradeOutcome | None:
        """Evaluate a single trade's outcome at 15m, 1h, 4h."""
        if not trade.traded_at:
            return None

        notional = trade.shares * trade.price
        if notional < self.min_notional:
            return None

        t0 = trade.traded_at
        price_t0 = trade.price

        # Get prices at future timestamps
        price_15m = self.get_price_at_time(session, trade.market_id, t0 + timedelta(minutes=15))
        price_1h = self.get_price_at_time(session, trade.market_id, t0 + timedelta(hours=1))
        price_4h = self.get_price_at_time(session, trade.market_id, t0 + timedelta(hours=4))

        # Calculate if each horizon was correct
        correct_15m = is_favorable_move(trade.side, price_t0, price_15m) if price_15m else False
        correct_1h = is_favorable_move(trade.side, price_t0, price_1h) if price_1h else False
        correct_4h = is_favorable_move(trade.side, price_t0, price_4h) if price_4h else False

        return TradeOutcome(
            trade_id=trade.id,
            wallet_address=trade.wallet_address,
            side=trade.side,
            price_at_trade=price_t0,
            price_15m=price_15m,
            price_1h=price_1h,
            price_4h=price_4h,
            correct_15m=correct_15m,
            correct_1h=correct_1h,
            correct_4h=correct_4h,
            delta_15m=calculate_delta(trade.side, price_t0, price_15m),
            delta_1h=calculate_delta(trade.side, price_t0, price_1h),
            delta_4h=calculate_delta(trade.side, price_t0, price_4h),
            notional=notional,
        )

    def compute_accuracy_score(self, stats: dict[str, Any]) -> Decimal | None:
        """Compute weighted accuracy score from individual horizon accuracies."""
        evaluated = stats.get("evaluated_trades", 0)
        if evaluated < self.min_evaluated_trades:
            return None

        acc_15m = Decimal(stats.get("correct_15m", 0)) / Decimal(evaluated)
        acc_1h = Decimal(stats.get("correct_1h", 0)) / Decimal(evaluated)
        acc_4h = Decimal(stats.get("correct_4h", 0)) / Decimal(evaluated)

        weighted = (
            acc_15m * ACCURACY_WEIGHTS["15m"]
            + acc_1h * ACCURACY_WEIGHTS["1h"]
            + acc_4h * ACCURACY_WEIGHTS["4h"]
        )
        return weighted

    def update_wallet_stats(
        self,
        session: Session,
        outcomes: list[TradeOutcome],
    ) -> int:
        """Aggregate outcomes and upsert wallet stats."""
        if not outcomes:
            return 0

        # Group by wallet
        wallet_data: dict[str, dict[str, Any]] = {}
        for outcome in outcomes:
            wallet = outcome.wallet_address
            if wallet not in wallet_data:
                wallet_data[wallet] = {
                    "total_trades": 0,
                    "evaluated_trades": 0,
                    "correct_15m": 0,
                    "correct_1h": 0,
                    "correct_4h": 0,
                    "total_notional": Decimal("0"),
                    "sum_delta_when_correct": Decimal("0"),
                    "correct_count": 0,
                }

            data = wallet_data[wallet]
            data["total_trades"] += 1
            data["evaluated_trades"] += 1
            data["total_notional"] += outcome.notional

            if outcome.correct_15m:
                data["correct_15m"] += 1
            if outcome.correct_1h:
                data["correct_1h"] += 1
            if outcome.correct_4h:
                data["correct_4h"] += 1

            # Track average delta when correct (use 4h as primary)
            if outcome.correct_4h and outcome.delta_4h:
                data["sum_delta_when_correct"] += outcome.delta_4h
                data["correct_count"] += 1

        # Upsert each wallet's stats
        updated = 0
        for wallet, data in wallet_data.items():
            accuracy_score = self.compute_accuracy_score(data)
            avg_delta = None
            if data["correct_count"] > 0:
                avg_delta = data["sum_delta_when_correct"] / Decimal(data["correct_count"])

            stmt = insert(WalletStats).values(
                wallet_address=wallet,
                total_trades=data["total_trades"],
                evaluated_trades=data["evaluated_trades"],
                correct_15m=data["correct_15m"],
                correct_1h=data["correct_1h"],
                correct_4h=data["correct_4h"],
                accuracy_score=accuracy_score,
                avg_delta_when_correct=avg_delta,
                total_notional=data["total_notional"],
                current_streak=data["correct_4h"],  # Simplified streak
                best_streak=data["correct_4h"],
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["wallet_address"],
                set_={
                    "total_trades": WalletStats.total_trades + data["total_trades"],
                    "evaluated_trades": WalletStats.evaluated_trades + data["evaluated_trades"],
                    "correct_15m": WalletStats.correct_15m + data["correct_15m"],
                    "correct_1h": WalletStats.correct_1h + data["correct_1h"],
                    "correct_4h": WalletStats.correct_4h + data["correct_4h"],
                    "total_notional": WalletStats.total_notional + data["total_notional"],
                    # Recalculate accuracy on update would require a trigger or post-update query
                    # For now, we'll update it in the backfill script
                },
            )
            session.execute(stmt)
            updated += 1

        return updated

    def get_smart_wallets(self, session: Session) -> list[WalletStats]:
        """Get wallets that meet the 'smart money' criteria."""
        return (
            session.execute(
                select(WalletStats)
                .where(
                    WalletStats.evaluated_trades >= self.min_evaluated_trades,
                    WalletStats.accuracy_score >= self.min_accuracy,
                    WalletStats.total_notional >= self.min_notional,
                )
                .order_by(WalletStats.accuracy_score.desc())
            )
            .scalars()
            .all()
        )

    def get_wallet_accuracy(self, session: Session, wallet_address: str) -> WalletStats | None:
        """Get accuracy stats for a specific wallet."""
        return session.execute(
            select(WalletStats).where(WalletStats.wallet_address == wallet_address)
        ).scalar_one_or_none()


__all__ = [
    "WalletAccuracyScorer",
    "TradeOutcome",
    "is_favorable_move",
    "MIN_ACCURACY_THRESHOLD",
    "MIN_EVALUATED_TRADES",
]
