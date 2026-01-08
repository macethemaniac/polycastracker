from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from polymarket_watch.models import Alert, Base, Market, SignalEvent, Trade
from services.scoring.aggregator import ScoringAggregator
from services.signals.engine import SignalEngine, TradeEnvelope


def build_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def seed_trades(session: Session, market: Market) -> list[Trade]:
    now = datetime.now(timezone.utc)
    trades = [
        Trade(
            market_id=market.id,
            wallet_address="w1",
            side="buy",
            shares=Decimal("10"),
            price=Decimal("0.6"),
            traded_at=now - timedelta(minutes=10),
        ),
        Trade(
            market_id=market.id,
            wallet_address="w1",
            side="buy",
            shares=Decimal("12"),
            price=Decimal("0.61"),
            traded_at=now - timedelta(minutes=5),
        ),
        Trade(
            market_id=market.id,
            wallet_address="w2",
            side="buy",
            shares=Decimal("9"),
            price=Decimal("0.62"),
            traded_at=now - timedelta(minutes=1),
        ),
    ]
    session.add_all(trades)
    session.commit()
    return trades


def test_replay_is_deterministic_with_seeded_trades():
    session = build_session()
    market = Market(external_id="m1", name="Test", status="active")
    session.add(market)
    session.commit()

    trades = seed_trades(session, market)

    engine = SignalEngine()
    # Lower thresholds so the tiny fixture reliably emits signals.
    engine.BIG_NOTIONAL = Decimal("1")
    engine.REPEAT_MIN_COUNT = 2
    engine.REPEAT_WINDOW = timedelta(minutes=30)
    engine.IMPACT_MIN_NOTIONAL = Decimal("1")
    engine.IMPACT_DEVIATION = Decimal("0.01")
    engine.CLUSTER_MIN_WALLETS = 2
    engine.CLUSTER_MIN_NOTIONAL = Decimal("1")
    scorer = ScoringAggregator(high_threshold=1.5, watch_threshold=0.5)

    def run_once():
        # Ensure deterministic state by clearing derived tables before each run.
        session.execute(delete(SignalEvent))
        session.execute(delete(Alert))
        session.commit()

        envelopes = [
            TradeEnvelope(
                id=t.id,
                market_id=t.market_id,
                wallet_address=t.wallet_address,
                side=t.side,
                shares=t.shares,
                price=t.price,
                traded_at=t.traded_at,
            )
            for t in sorted(trades, key=lambda x: x.traded_at)
        ]
        signals = engine.evaluate(session, envelopes)
        session.add_all(
            [
                SignalEvent(
                    market_id=s.market_id,
                    wallet_address=s.wallet_address,
                    side=s.side,
                    signal_type=s.signal_type,
                    severity=s.severity,
                    score=s.score,
                    details_json=s.details,
                    observed_at=s.observed_at,
                )
                for s in signals
            ]
        )
        session.commit()
        scorer.process(session)
        alerts = session.execute(select(Alert)).scalars().all()
        return len(signals), len(alerts), alerts[0].score if alerts else None

    first = run_once()
    second = run_once()

    assert first == second
    assert first[0] > 0  # signals produced
    assert first[1] >= 1  # alerts created/deduped
