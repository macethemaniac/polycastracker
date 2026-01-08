from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterable, List

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymarket_watch.config import settings
from polymarket_watch.logging import setup_logging
from polymarket_watch.models import AppState, SignalEvent, Trade
from polymarket_watch.state import default_state

from .engine import Signal, SignalEngine, TradeEnvelope

logger = logging.getLogger(__name__)

SIGNAL_CURSOR_KEY = "cursor:signals:last_trade_at"
BATCH_SIZE = 200
IDLE_SLEEP_SECONDS = 5
BACKOFF_BASE_SECONDS = 5
BACKOFF_MAX_SECONDS = 120


def _load_cursor(session: Session) -> datetime | None:
    row = session.execute(select(AppState).where(AppState.key == SIGNAL_CURSOR_KEY)).scalar_one_or_none()
    if row and row.value:
        try:
            dt = datetime.fromisoformat(row.value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _store_cursor(session: Session, value: datetime) -> None:
    iso = value.isoformat()
    stmt = insert(AppState).values(key=SIGNAL_CURSOR_KEY, value=iso).on_conflict_do_update(
        index_elements=[AppState.key], set_={"value": iso}
    )
    session.execute(stmt)


def _fetch_trades(session: Session, cursor: datetime | None, limit: int) -> List[TradeEnvelope]:
    query = select(Trade).order_by(Trade.traded_at).limit(limit)
    if cursor:
        query = query.where(Trade.traded_at > cursor)
    result = session.execute(query)
    trades: List[TradeEnvelope] = []
    for trade in result.scalars():
        trades.append(
            TradeEnvelope(
                id=trade.id,
                market_id=trade.market_id,
                wallet_address=trade.wallet_address,
                side=trade.side,
                shares=trade.shares,
                price=trade.price,
                traded_at=trade.traded_at,
            )
        )
    return trades


def _insert_signals(session: Session, signals: Iterable[Signal]) -> int:
    signals_list = list(signals)
    if not signals_list:
        return 0
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
        for s in signals_list
    ]
    session.execute(insert(SignalEvent).values(values))
    return len(values)


def run_worker() -> None:
    cfg = settings
    setup_logging(cfg)
    state = default_state()
    engine = SignalEngine()
    backoff_attempt = 0

    logger.info("Starting signals worker")

    while True:
        try:
            with state.session_factory() as session:
                with session.begin():
                    cursor = _load_cursor(session)
                    trades = _fetch_trades(session, cursor, BATCH_SIZE)
                    if not trades:
                        backoff_attempt = 0
                        # No new trades; don't advance cursor
                        raise StopIteration

                    signals = engine.evaluate(session, trades)
                    inserted = _insert_signals(session, signals)
                    latest_at = trades[-1].traded_at
                    _store_cursor(session, latest_at)
                    logger.info(
                        "Processed trades for signals",
                        extra={"trades": len(trades), "signals": inserted, "cursor": latest_at.isoformat()},
                    )
            backoff_attempt = 0
        except StopIteration:
            time.sleep(IDLE_SLEEP_SECONDS)
            continue
        except Exception:
            logger.exception("Signals worker error")
            backoff = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2**backoff_attempt))
            backoff_attempt += 1
            time.sleep(backoff)


def main() -> None:
    run_worker()


if __name__ == "__main__":
    main()
