from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from polymarket_watch.models import Trade, WalletStats


@dataclass
class TradeEnvelope:
    id: int
    market_id: int
    wallet_address: str
    side: str
    shares: Decimal
    price: Decimal
    traded_at: datetime


@dataclass
class Signal:
    market_id: int
    wallet_address: str | None
    side: str | None
    signal_type: str
    severity: str
    score: float
    details: dict[str, Any]
    observed_at: datetime


class SignalEngine:
    BIG_NOTIONAL = Decimal("1000")
    LOW_ACTIVITY_WINDOW = timedelta(hours=24)
    LOW_ACTIVITY_MAX_TRADES = 2
    REPEAT_WINDOW = timedelta(minutes=10)
    REPEAT_MIN_COUNT = 3
    IMPACT_DEVIATION = Decimal("0.05")
    IMPACT_MIN_NOTIONAL = Decimal("500")
    CLUSTER_WINDOW = timedelta(minutes=5)
    CLUSTER_MIN_WALLETS = 3
    CLUSTER_MIN_NOTIONAL = Decimal("200")
    # Early positioning thresholds
    SMART_WALLET_MIN_ACCURACY = Decimal("0.60")
    SMART_WALLET_MIN_TRADES = 5
    SMART_WALLET_MIN_NOTIONAL = Decimal("100")

    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime.now(timezone.utc)

    def _load_wallet_history(self, session: Session, wallets: set[str], before: datetime) -> dict[str, dict[str, Any]]:
        if not wallets:
            return {}
        history: dict[str, dict[str, Any]] = defaultdict(lambda: {"first_seen": None, "recent": 0})
        recent_cutoff = before - self.LOW_ACTIVITY_WINDOW
        rows = session.execute(
            select(Trade.wallet_address, Trade.traded_at)
            .where(Trade.wallet_address.in_(wallets), Trade.traded_at < before)
        ).all()
        for wallet, traded_at in rows:
            record = history[wallet]
            record["first_seen"] = traded_at if record["first_seen"] is None else min(record["first_seen"], traded_at)
            if traded_at >= recent_cutoff:
                record["recent"] += 1
        return history

    def _load_market_price_history(
        self, session: Session, markets: set[int], before: datetime, limit_per_market: int = 50
    ) -> dict[int, deque[tuple[datetime, Decimal]]]:
        histories: dict[int, deque[tuple[datetime, Decimal]]] = {m: deque(maxlen=limit_per_market) for m in markets}
        if not markets:
            return histories
        rows = session.execute(
            select(Trade.market_id, Trade.traded_at, Trade.price)
            .where(Trade.market_id.in_(markets), Trade.traded_at < before)
            .order_by(Trade.market_id, Trade.traded_at)
        ).all()
        for market_id, traded_at, price in rows:
            histories[market_id].append((traded_at, Decimal(price)))
        return histories

    def _notional(self, shares: Decimal, price: Decimal) -> Decimal:
        return shares * price

    def _baseline_price(self, history: deque[tuple[datetime, Decimal]]) -> Decimal | None:
        if not history:
            return None
        prices = [p for _, p in list(history)[-10:]]
        if not prices:
            return None
        return sum(prices) / Decimal(len(prices))

    def _load_wallet_stats(self, session: Session, wallets: set[str]) -> dict[str, WalletStats]:
        """Load wallet accuracy stats for smart wallet detection."""
        if not wallets:
            return {}
        rows = session.execute(
            select(WalletStats)
            .where(
                WalletStats.wallet_address.in_(wallets),
                WalletStats.evaluated_trades >= self.SMART_WALLET_MIN_TRADES,
                WalletStats.accuracy_score >= self.SMART_WALLET_MIN_ACCURACY,
            )
        ).scalars().all()
        return {w.wallet_address: w for w in rows}

    def evaluate(self, session: Session, trades: Iterable[TradeEnvelope]) -> list[Signal]:
        trade_list = sorted(trades, key=lambda t: t.traded_at)
        if not trade_list:
            return []

        wallets = {t.wallet_address for t in trade_list if t.wallet_address}
        markets = {t.market_id for t in trade_list}
        earliest = trade_list[0].traded_at

        wallet_history = self._load_wallet_history(session, wallets, earliest)
        market_price_history = self._load_market_price_history(session, markets, earliest)
        wallet_stats = self._load_wallet_stats(session, wallets)

        repeat_windows: dict[tuple[str, int, str], deque[datetime]] = defaultdict(deque)
        cluster_windows: dict[tuple[int, str], deque[tuple[datetime, str, Decimal]]] = defaultdict(deque)

        signals: list[Signal] = []

        for trade in trade_list:
            notional = self._notional(trade.shares, trade.price)
            wallet = trade.wallet_address
            side = trade.side

            # FRESH_WALLET_BIG_SIZE
            stats = wallet_history.get(wallet, {"first_seen": None, "recent": 0})
            if stats.get("first_seen") is None:
                if notional >= self.BIG_NOTIONAL:
                    signals.append(
                        Signal(
                            market_id=trade.market_id,
                            wallet_address=wallet,
                            side=side,
                            signal_type="FRESH_WALLET_BIG_SIZE",
                            severity="high",
                            score=float(notional),
                            details={
                                "notional": str(notional),
                                "shares": str(trade.shares),
                                "price": str(trade.price),
                                "thresholds": {"big_notional": str(self.BIG_NOTIONAL)},
                                "why": "First time wallet seen with large trade",
                            },
                            observed_at=trade.traded_at,
                        )
                    )

            # LOW_ACTIVITY_WALLET_BIG_SIZE
            recent_count = stats.get("recent", 0)
            if recent_count <= self.LOW_ACTIVITY_MAX_TRADES and notional >= self.BIG_NOTIONAL:
                signals.append(
                    Signal(
                        market_id=trade.market_id,
                        wallet_address=wallet,
                        side=side,
                        signal_type="LOW_ACTIVITY_WALLET_BIG_SIZE",
                        severity="medium",
                        score=float(notional),
                        details={
                            "notional": str(notional),
                            "recent_trades": recent_count,
                            "window_hours": self.LOW_ACTIVITY_WINDOW.total_seconds() / 3600,
                            "thresholds": {
                                "max_recent_trades": self.LOW_ACTIVITY_MAX_TRADES,
                                "big_notional": str(self.BIG_NOTIONAL),
                            },
                            "why": "Low activity wallet executed a large trade",
                        },
                        observed_at=trade.traded_at,
                    )
                )

            # REPEAT_ENTRIES
            repeat_key = (wallet, trade.market_id, side)
            repeat_window = repeat_windows[repeat_key]
            repeat_window.append(trade.traded_at)
            while repeat_window and trade.traded_at - repeat_window[0] > self.REPEAT_WINDOW:
                repeat_window.popleft()
            if len(repeat_window) >= self.REPEAT_MIN_COUNT:
                signals.append(
                    Signal(
                        market_id=trade.market_id,
                        wallet_address=wallet,
                        side=side,
                        signal_type="REPEAT_ENTRIES",
                        severity="medium",
                        score=float(len(repeat_window)),
                        details={
                            "count": len(repeat_window),
                            "window_minutes": self.REPEAT_WINDOW.total_seconds() / 60,
                            "why": "Multiple entries by same wallet/side in short window",
                        },
                        observed_at=trade.traded_at,
                    )
                )

            # THIN_MARKET_IMPACT
            history = market_price_history.get(trade.market_id)
            baseline = self._baseline_price(history) if history is not None else None
            if baseline and baseline > 0 and notional >= self.IMPACT_MIN_NOTIONAL:
                deviation = abs(trade.price - baseline) / baseline
                if deviation >= self.IMPACT_DEVIATION:
                    signals.append(
                        Signal(
                            market_id=trade.market_id,
                            wallet_address=wallet,
                            side=side,
                            signal_type="THIN_MARKET_IMPACT",
                            severity="high" if deviation >= self.IMPACT_DEVIATION * 2 else "medium",
                            score=float(deviation),
                            details={
                                "price": str(trade.price),
                                "baseline_price": str(baseline),
                                "deviation_pct": float(deviation),
                                "notional": str(notional),
                                "thresholds": {
                                    "impact_deviation": float(self.IMPACT_DEVIATION),
                                    "min_notional": str(self.IMPACT_MIN_NOTIONAL),
                                },
                                "why": "Trade price deviates from recent baseline",
                            },
                            observed_at=trade.traded_at,
                        )
                    )
            # Update history after impact check
            if history is not None:
                history.append((trade.traded_at, trade.price))

            # CLUSTERING
            cluster_key = (trade.market_id, side)
            cluster_window = cluster_windows[cluster_key]
            cluster_window.append((trade.traded_at, wallet, notional))
            cutoff = trade.traded_at - self.CLUSTER_WINDOW
            while cluster_window and cluster_window[0][0] < cutoff:
                cluster_window.popleft()
            wallets_in_window = {w for _, w, _ in cluster_window}
            if len(wallets_in_window) >= self.CLUSTER_MIN_WALLETS:
                total_notional = sum(n for _, _, n in cluster_window)
                if total_notional >= self.CLUSTER_MIN_NOTIONAL * len(wallets_in_window):
                    signals.append(
                        Signal(
                            market_id=trade.market_id,
                            wallet_address=wallet,
                            side=side,
                            signal_type="CLUSTERING",
                            severity="medium",
                            score=float(total_notional),
                            details={
                                "unique_wallets": len(wallets_in_window),
                                "window_minutes": self.CLUSTER_WINDOW.total_seconds() / 60,
                                "total_notional": str(total_notional),
                                "thresholds": {
                                    "min_wallets": self.CLUSTER_MIN_WALLETS,
                                    "min_notional_per_wallet": str(self.CLUSTER_MIN_NOTIONAL),
                                },
                                "why": "Multiple wallets trading same side in short window",
                            },
                            observed_at=trade.traded_at,
                        )
                    )

            # EARLY_POSITIONING - Smart wallet detected
            smart_wallet = wallet_stats.get(wallet)
            if smart_wallet and notional >= self.SMART_WALLET_MIN_NOTIONAL:
                accuracy = float(smart_wallet.accuracy_score or 0)
                severity = "high" if accuracy >= 0.75 else "medium"
                signals.append(
                    Signal(
                        market_id=trade.market_id,
                        wallet_address=wallet,
                        side=side,
                        signal_type="EARLY_POSITIONING",
                        severity=severity,
                        score=accuracy * float(notional),
                        details={
                            "notional": str(notional),
                            "wallet_accuracy": accuracy,
                            "wallet_evaluated_trades": smart_wallet.evaluated_trades,
                            "wallet_correct_4h": smart_wallet.correct_4h,
                            "wallet_total_notional": str(smart_wallet.total_notional),
                            "wallet_best_streak": smart_wallet.best_streak,
                            "thresholds": {
                                "min_accuracy": float(self.SMART_WALLET_MIN_ACCURACY),
                                "min_trades": self.SMART_WALLET_MIN_TRADES,
                                "min_notional": str(self.SMART_WALLET_MIN_NOTIONAL),
                            },
                            "why": f"Wallet has {accuracy:.0%} historical accuracy over {smart_wallet.evaluated_trades} trades",
                        },
                        observed_at=trade.traded_at,
                    )
                )

            # Update wallet recency counts
            if trade.traded_at >= earliest - self.LOW_ACTIVITY_WINDOW:
                stats["recent"] = stats.get("recent", 0) + 1
                stats["first_seen"] = stats.get("first_seen") or trade.traded_at
                wallet_history[wallet] = stats

        return signals


__all__ = ["SignalEngine", "Signal", "TradeEnvelope"]
