#!/usr/bin/env python
"""Backfill wallet_stats table from historical trade data.

This script evaluates all historical trades and computes accuracy metrics
for each wallet, enabling the EARLY_POSITIONING signal detection.

Usage:
    poetry run python scripts/backfill_wallet_stats.py [--batch-size 1000] [--min-age-hours 4]
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, func, delete
from sqlalchemy.dialects.postgresql import insert

from polymarket_watch.config import settings
from polymarket_watch.logging import setup_logging
from polymarket_watch.models import Trade, WalletStats
from polymarket_watch.state import default_state
from services.profiling.accuracy import (
    WalletAccuracyScorer,
    TradeOutcome,
    MIN_FAVORABLE_DELTA,
    ACCURACY_WEIGHTS,
)

logger = logging.getLogger(__name__)


def backfill_wallet_stats(
    batch_size: int = 1000,
    min_age_hours: int = 4,
    reset: bool = False,
) -> None:
    """Backfill wallet stats from historical trades.

    Args:
        batch_size: Number of trades to process per batch
        min_age_hours: Only evaluate trades older than this (need future price data)
        reset: If True, clear existing wallet_stats before backfilling
    """
    setup_logging(settings)
    state = default_state()
    scorer = WalletAccuracyScorer()

    # Cutoff: only evaluate trades old enough to have 4h price data
    cutoff = datetime.now(timezone.utc) - timedelta(hours=min_age_hours)

    with state.session_factory() as session:
        if reset:
            logger.info("Resetting wallet_stats table...")
            session.execute(delete(WalletStats))
            session.commit()

        # Count total trades to process
        total_trades = session.execute(
            select(func.count(Trade.id)).where(Trade.traded_at < cutoff)
        ).scalar() or 0
        logger.info(f"Found {total_trades} trades to evaluate (older than {min_age_hours}h)")

        if total_trades == 0:
            logger.info("No trades to process")
            return

        # Get unique wallets
        wallets = session.execute(
            select(Trade.wallet_address)
            .where(Trade.traded_at < cutoff)
            .distinct()
        ).scalars().all()
        logger.info(f"Processing {len(wallets)} unique wallets")

        # Process wallet by wallet for better accuracy calculation
        processed_wallets = 0
        for wallet_address in wallets:
            trades = session.execute(
                select(Trade)
                .where(
                    Trade.wallet_address == wallet_address,
                    Trade.traded_at < cutoff,
                )
                .order_by(Trade.traded_at)
            ).scalars().all()

            if not trades:
                continue

            # Evaluate each trade
            outcomes: list[TradeOutcome] = []
            for trade in trades:
                outcome = scorer.evaluate_trade(session, trade)
                if outcome:
                    outcomes.append(outcome)

            if not outcomes:
                continue

            # Aggregate stats for this wallet
            stats = {
                "total_trades": len(trades),
                "evaluated_trades": len(outcomes),
                "correct_15m": sum(1 for o in outcomes if o.correct_15m),
                "correct_1h": sum(1 for o in outcomes if o.correct_1h),
                "correct_4h": sum(1 for o in outcomes if o.correct_4h),
                "total_notional": sum(o.notional for o in outcomes),
            }

            # Calculate accuracy score
            if stats["evaluated_trades"] >= scorer.min_evaluated_trades:
                acc_15m = Decimal(stats["correct_15m"]) / Decimal(stats["evaluated_trades"])
                acc_1h = Decimal(stats["correct_1h"]) / Decimal(stats["evaluated_trades"])
                acc_4h = Decimal(stats["correct_4h"]) / Decimal(stats["evaluated_trades"])
                accuracy_score = (
                    acc_15m * ACCURACY_WEIGHTS["15m"]
                    + acc_1h * ACCURACY_WEIGHTS["1h"]
                    + acc_4h * ACCURACY_WEIGHTS["4h"]
                )
            else:
                accuracy_score = None

            # Calculate average delta when correct
            correct_deltas = [o.delta_4h for o in outcomes if o.correct_4h and o.delta_4h]
            avg_delta = sum(correct_deltas) / len(correct_deltas) if correct_deltas else None

            # Calculate streaks
            current_streak = 0
            best_streak = 0
            for o in outcomes:
                if o.correct_4h:
                    current_streak += 1
                    best_streak = max(best_streak, current_streak)
                else:
                    current_streak = 0

            # Upsert wallet stats
            stmt = insert(WalletStats).values(
                wallet_address=wallet_address,
                total_trades=stats["total_trades"],
                evaluated_trades=stats["evaluated_trades"],
                correct_15m=stats["correct_15m"],
                correct_1h=stats["correct_1h"],
                correct_4h=stats["correct_4h"],
                accuracy_score=accuracy_score,
                avg_delta_when_correct=avg_delta,
                total_notional=stats["total_notional"],
                current_streak=current_streak,
                best_streak=best_streak,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["wallet_address"],
                set_={
                    "total_trades": stats["total_trades"],
                    "evaluated_trades": stats["evaluated_trades"],
                    "correct_15m": stats["correct_15m"],
                    "correct_1h": stats["correct_1h"],
                    "correct_4h": stats["correct_4h"],
                    "accuracy_score": accuracy_score,
                    "avg_delta_when_correct": avg_delta,
                    "total_notional": stats["total_notional"],
                    "current_streak": current_streak,
                    "best_streak": best_streak,
                },
            )
            session.execute(stmt)

            processed_wallets += 1
            if processed_wallets % 100 == 0:
                session.commit()
                logger.info(f"Processed {processed_wallets}/{len(wallets)} wallets")

        session.commit()
        logger.info(f"Completed processing {processed_wallets} wallets")

        # Log summary stats
        smart_wallets = session.execute(
            select(func.count(WalletStats.id))
            .where(
                WalletStats.evaluated_trades >= scorer.min_evaluated_trades,
                WalletStats.accuracy_score >= scorer.min_accuracy,
            )
        ).scalar() or 0
        logger.info(f"Found {smart_wallets} 'smart wallets' (>={scorer.min_accuracy:.0%} accuracy)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill wallet accuracy stats")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Trades to process per batch",
    )
    parser.add_argument(
        "--min-age-hours",
        type=int,
        default=4,
        help="Minimum trade age in hours (need future price data)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing wallet_stats before backfilling",
    )
    args = parser.parse_args()

    backfill_wallet_stats(
        batch_size=args.batch_size,
        min_age_hours=args.min_age_hours,
        reset=args.reset,
    )


if __name__ == "__main__":
    main()
