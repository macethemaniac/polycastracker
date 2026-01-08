"""Tests for early positioning signal detection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from polymarket_watch.models import Base, Market, Trade, WalletStats
from services.signals.engine import SignalEngine, TradeEnvelope
from services.profiling.accuracy import (
    WalletAccuracyScorer,
    is_favorable_move,
    calculate_delta,
)


def build_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def add_market(session: Session) -> Market:
    market = Market(external_id="m1", name="Test Market", category=None, status="active")
    session.add(market)
    session.commit()
    return market


def add_smart_wallet(session: Session, wallet_address: str, accuracy: Decimal) -> WalletStats:
    """Create a wallet with high historical accuracy."""
    stats = WalletStats(
        wallet_address=wallet_address,
        total_trades=20,
        evaluated_trades=15,
        correct_15m=10,
        correct_1h=11,
        correct_4h=12,
        accuracy_score=accuracy,
        avg_delta_when_correct=Decimal("0.08"),
        total_notional=Decimal("5000"),
        current_streak=3,
        best_streak=5,
    )
    session.add(stats)
    session.commit()
    return stats


class TestIsFavorableMove:
    """Test the is_favorable_move helper function."""

    def test_buy_price_up_is_favorable(self):
        assert is_favorable_move("buy", Decimal("0.50"), Decimal("0.60")) is True

    def test_buy_price_down_is_not_favorable(self):
        assert is_favorable_move("buy", Decimal("0.50"), Decimal("0.40")) is False

    def test_sell_price_down_is_favorable(self):
        assert is_favorable_move("sell", Decimal("0.50"), Decimal("0.40")) is True

    def test_sell_price_up_is_not_favorable(self):
        assert is_favorable_move("sell", Decimal("0.50"), Decimal("0.60")) is False

    def test_small_move_is_not_favorable(self):
        # Less than 5% move should not count
        assert is_favorable_move("buy", Decimal("0.50"), Decimal("0.52")) is False


class TestCalculateDelta:
    """Test the calculate_delta helper function."""

    def test_buy_positive_delta(self):
        delta = calculate_delta("buy", Decimal("0.50"), Decimal("0.60"))
        assert delta == Decimal("0.10")

    def test_buy_negative_delta(self):
        delta = calculate_delta("buy", Decimal("0.50"), Decimal("0.40"))
        assert delta == Decimal("-0.10")

    def test_sell_inverts_delta(self):
        # For sells, price going down is positive (favorable)
        delta = calculate_delta("sell", Decimal("0.50"), Decimal("0.40"))
        assert delta == Decimal("0.10")

    def test_none_price_returns_none(self):
        assert calculate_delta("buy", Decimal("0.50"), None) is None


class TestEarlyPositioningSignal:
    """Test the EARLY_POSITIONING signal in SignalEngine."""

    def test_smart_wallet_triggers_early_positioning_signal(self):
        session = build_session()
        market = add_market(session)
        add_smart_wallet(session, "smart_wallet_1", Decimal("0.75"))
        now = datetime.now(timezone.utc)

        engine = SignalEngine()
        engine.SMART_WALLET_MIN_ACCURACY = Decimal("0.60")
        engine.SMART_WALLET_MIN_TRADES = 5
        engine.SMART_WALLET_MIN_NOTIONAL = Decimal("50")

        trades = [
            TradeEnvelope(
                id=1,
                market_id=market.id,
                wallet_address="smart_wallet_1",
                side="buy",
                shares=Decimal("100"),
                price=Decimal("0.60"),
                traded_at=now,
            )
        ]

        signals = engine.evaluate(session, trades)

        early_pos_signals = [s for s in signals if s.signal_type == "EARLY_POSITIONING"]
        assert len(early_pos_signals) == 1

        signal = early_pos_signals[0]
        assert signal.severity == "high"  # 75% accuracy >= 75% threshold
        assert signal.wallet_address == "smart_wallet_1"
        assert "75%" in signal.details["why"]

    def test_regular_wallet_does_not_trigger_early_positioning(self):
        session = build_session()
        market = add_market(session)
        now = datetime.now(timezone.utc)

        engine = SignalEngine()

        trades = [
            TradeEnvelope(
                id=1,
                market_id=market.id,
                wallet_address="regular_wallet",
                side="buy",
                shares=Decimal("100"),
                price=Decimal("0.60"),
                traded_at=now,
            )
        ]

        signals = engine.evaluate(session, trades)

        early_pos_signals = [s for s in signals if s.signal_type == "EARLY_POSITIONING"]
        assert len(early_pos_signals) == 0

    def test_low_accuracy_wallet_does_not_trigger(self):
        session = build_session()
        market = add_market(session)
        # Accuracy below threshold
        add_smart_wallet(session, "low_acc_wallet", Decimal("0.45"))
        now = datetime.now(timezone.utc)

        engine = SignalEngine()
        engine.SMART_WALLET_MIN_ACCURACY = Decimal("0.60")

        trades = [
            TradeEnvelope(
                id=1,
                market_id=market.id,
                wallet_address="low_acc_wallet",
                side="buy",
                shares=Decimal("100"),
                price=Decimal("0.60"),
                traded_at=now,
            )
        ]

        signals = engine.evaluate(session, trades)

        early_pos_signals = [s for s in signals if s.signal_type == "EARLY_POSITIONING"]
        assert len(early_pos_signals) == 0

    def test_medium_accuracy_wallet_triggers_medium_severity(self):
        session = build_session()
        market = add_market(session)
        # Accuracy between 60-75% should be medium severity
        add_smart_wallet(session, "medium_acc_wallet", Decimal("0.65"))
        now = datetime.now(timezone.utc)

        engine = SignalEngine()
        engine.SMART_WALLET_MIN_ACCURACY = Decimal("0.60")
        engine.SMART_WALLET_MIN_NOTIONAL = Decimal("50")

        trades = [
            TradeEnvelope(
                id=1,
                market_id=market.id,
                wallet_address="medium_acc_wallet",
                side="buy",
                shares=Decimal("100"),
                price=Decimal("0.60"),
                traded_at=now,
            )
        ]

        signals = engine.evaluate(session, trades)

        early_pos_signals = [s for s in signals if s.signal_type == "EARLY_POSITIONING"]
        assert len(early_pos_signals) == 1
        assert early_pos_signals[0].severity == "medium"

    def test_small_trade_does_not_trigger(self):
        session = build_session()
        market = add_market(session)
        add_smart_wallet(session, "smart_wallet_2", Decimal("0.80"))
        now = datetime.now(timezone.utc)

        engine = SignalEngine()
        engine.SMART_WALLET_MIN_NOTIONAL = Decimal("100")

        # Trade notional = 10 * 0.60 = 6, below threshold
        trades = [
            TradeEnvelope(
                id=1,
                market_id=market.id,
                wallet_address="smart_wallet_2",
                side="buy",
                shares=Decimal("10"),
                price=Decimal("0.60"),
                traded_at=now,
            )
        ]

        signals = engine.evaluate(session, trades)

        early_pos_signals = [s for s in signals if s.signal_type == "EARLY_POSITIONING"]
        assert len(early_pos_signals) == 0


class TestWalletAccuracyScorer:
    """Test the WalletAccuracyScorer service."""

    def test_get_smart_wallets(self):
        session = build_session()
        add_smart_wallet(session, "smart_1", Decimal("0.75"))
        add_smart_wallet(session, "smart_2", Decimal("0.80"))
        add_smart_wallet(session, "not_smart", Decimal("0.40"))

        scorer = WalletAccuracyScorer(min_accuracy=Decimal("0.60"))
        smart_wallets = scorer.get_smart_wallets(session)

        addresses = {w.wallet_address for w in smart_wallets}
        assert "smart_1" in addresses
        assert "smart_2" in addresses
        assert "not_smart" not in addresses

    def test_get_wallet_accuracy(self):
        session = build_session()
        add_smart_wallet(session, "test_wallet", Decimal("0.70"))

        scorer = WalletAccuracyScorer()
        stats = scorer.get_wallet_accuracy(session, "test_wallet")

        assert stats is not None
        assert stats.accuracy_score == Decimal("0.70")
        assert stats.evaluated_trades == 15

    def test_get_wallet_accuracy_not_found(self):
        session = build_session()
        scorer = WalletAccuracyScorer()
        stats = scorer.get_wallet_accuracy(session, "nonexistent")
        assert stats is None
