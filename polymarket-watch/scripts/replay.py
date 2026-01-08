from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import delete, select

from polymarket_watch.config import settings
from polymarket_watch.logging import setup_logging
from polymarket_watch.models import Alert, AppState, SignalEvent, Trade
from polymarket_watch.state import default_state
from services.signals.engine import SignalEngine, TradeEnvelope
from services.scoring.aggregator import ScoringAggregator

logger = logging.getLogger(__name__)

SIGNAL_CURSOR_KEY = "cursor:signals:last_trade_at"
SCORING_CURSOR_KEY = "cursor:scoring:last_signal_id"


def reset_state(session) -> None:
    session.execute(delete(SignalEvent))
    session.execute(delete(Alert))
    session.execute(delete(AppState).where(AppState.key.in_([SIGNAL_CURSOR_KEY, SCORING_CURSOR_KEY])))


def replay(start: datetime, end: datetime, speed: float, batch_size: int) -> None:
    setup_logging()
    state = default_state()
    engine = SignalEngine()
    scorer = ScoringAggregator()

    with state.session_factory() as session:
        with session.begin():
            reset_state(session)

    logger.info("Starting replay", extra={"start": start.isoformat(), "end": end.isoformat()})

    offset = 0
    while True:
        with state.session_factory() as session:
            batch = (
                session.execute(
                    select(Trade)
                    .where(Trade.traded_at >= start, Trade.traded_at <= end)
                    .order_by(Trade.traded_at)
                    .offset(offset)
                    .limit(batch_size)
                )
                .scalars()
                .all()
            )
            if not batch:
                break

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
                for t in batch
            ]

            with state.session_factory() as write_session:
                with write_session.begin():
                    signals = engine.evaluate(write_session, envelopes)
                    if signals:
                        values = [
                            {
                                "market_id": s.market_id,
                                "wallet_address": s.wallet_address,
                                "wallet_profile_id": None,
                                "side": s.side,
                                "signal_type": s.signal_type,
                                "severity": s.severity,
                                "score": s.score,
                                "details_json": s.details,
                                "observed_at": s.observed_at,
                            }
                            for s in signals
                        ]
                        write_session.execute(SignalEvent.__table__.insert().values(values))
                        write_session.flush()
                    scorer.process(write_session)

            offset += len(batch)
            if speed > 0:
                time.sleep(speed)

    logger.info("Replay complete", extra={"processed_trades": offset})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay historical trades through signal/scoring stack")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--speed", type=float, default=0, help="Seconds to sleep between batches (0=fast)")
    parser.add_argument("--batch-size", type=int, default=500)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    replay(start=start, end=end, speed=args.speed, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
