from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymarket_watch.config import settings
from polymarket_watch.logging import setup_logging
from polymarket_watch.models import AppState, SignalEvent
from polymarket_watch.state import default_state

from .aggregator import ScoringAggregator

logger = logging.getLogger(__name__)

CURSOR_KEY = "cursor:scoring:last_signal_id"
IDLE_SLEEP_SECONDS = 10
BACKOFF_BASE_SECONDS = 5
BACKOFF_MAX_SECONDS = 180


def _load_cursor(session: Session) -> int | None:
    row = session.execute(select(AppState).where(AppState.key == CURSOR_KEY)).scalar_one_or_none()
    if row and row.value:
        try:
            return int(row.value)
        except ValueError:
            return None
    return None


def _store_cursor(session: Session, value: int) -> None:
    stmt = insert(AppState).values(key=CURSOR_KEY, value=str(value))
    stmt = stmt.on_conflict_do_update(index_elements=[AppState.key], set_={"value": str(value)})
    session.execute(stmt)


def _has_new_signals(session: Session, cursor: int | None) -> int | None:
    query = select(func.max(SignalEvent.id))
    if cursor is not None:
        query = query.where(SignalEvent.id > cursor)
    max_id = session.execute(query).scalar()
    return max_id


def run_worker() -> None:
    cfg = settings
    setup_logging(cfg)
    state = default_state()
    aggregator = ScoringAggregator()
    backoff_attempt = 0

    logger.info("Starting scoring worker")

    while True:
        try:
            with state.session_factory() as session:
                with session.begin():
                    cursor = _load_cursor(session)
                    max_new_id = _has_new_signals(session, cursor)
                    if not max_new_id:
                        raise StopIteration

                    processed = aggregator.process(session)
                    _store_cursor(session, max_new_id)
                    logger.info(
                        "Scoring iteration",
                        extra={"processed_signals": processed, "cursor": max_new_id},
                    )
            backoff_attempt = 0
        except StopIteration:
            time.sleep(IDLE_SLEEP_SECONDS)
            continue
        except Exception:
            logger.exception("Scoring worker error")
            backoff = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2**backoff_attempt))
            backoff_attempt += 1
            time.sleep(backoff)


def main() -> None:
    run_worker()


if __name__ == "__main__":
    main()
