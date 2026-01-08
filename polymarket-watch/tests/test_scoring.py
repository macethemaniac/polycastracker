from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from polymarket_watch.models import Alert, Base, Market, SignalEvent
from services.scoring.aggregator import ScoringAggregator


def build_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def add_market(session: Session) -> Market:
    market = Market(external_id="m1", name="Test", category=None, status="active")
    session.add(market)
    session.commit()
    return market


def test_scoring_creates_high_alert_with_bonus():
    session = build_session()
    market = add_market(session)
    now = datetime.now(timezone.utc)
    events = [
        SignalEvent(
            market_id=market.id,
            wallet_address="w1",
            side="buy",
            signal_type="FRESH_WALLET_BIG_SIZE",
            severity="high",
            observed_at=now - timedelta(minutes=5),
        ),
        SignalEvent(
            market_id=market.id,
            wallet_address="w2",
            side="buy",
            signal_type="THIN_MARKET_IMPACT",
            severity="medium",
            observed_at=now - timedelta(minutes=4),
        ),
        SignalEvent(
            market_id=market.id,
            wallet_address="w3",
            side="buy",
            signal_type="CLUSTERING",
            severity="medium",
            observed_at=now - timedelta(minutes=3),
        ),
    ]
    session.add_all(events)
    session.commit()

    aggregator = ScoringAggregator(high_threshold=8.0, watch_threshold=1.0)
    with session.begin():
        aggregator.process(session)

    alert = session.execute(select(Alert)).scalar_one()
    assert alert.status == "high"
    assert alert.score and alert.score >= 8.0
    assert alert.why_json
    assert "distinct_types" in alert.why_json


def test_scoring_updates_existing_alert_instead_of_new():
    session = build_session()
    market = add_market(session)
    now = datetime.now(timezone.utc)

    first_event = SignalEvent(
        market_id=market.id,
        wallet_address="w1",
        side="sell",
        signal_type="LOW_ACTIVITY_WALLET_BIG_SIZE",
        severity="medium",
        observed_at=now - timedelta(minutes=10),
    )
    session.add(first_event)
    session.commit()

    aggregator = ScoringAggregator(high_threshold=5.0, watch_threshold=1.0)
    with session.begin():
        aggregator.process(session)

    alert = session.execute(select(Alert)).scalar_one()
    first_alert_id = alert.id
    first_score = float(alert.score or 0)

    # Add another signal within window and ensure the alert is updated, not duplicated.
    second_event = SignalEvent(
        market_id=market.id,
        wallet_address="w2",
        side="sell",
        signal_type="REPEAT_ENTRIES",
        severity="medium",
        observed_at=now - timedelta(minutes=5),
    )
    session.add(second_event)
    session.commit()

    with session.begin():
        aggregator.process(session)

    alerts = session.execute(select(Alert)).scalars().all()
    assert len(alerts) == 1
    updated_alert = alerts[0]
    assert updated_alert.id == first_alert_id
    assert float(updated_alert.score or 0) > first_score
