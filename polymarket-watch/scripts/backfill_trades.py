from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from polymarket_watch.config import settings
from polymarket_watch.logging import setup_logging
from polymarket_watch.models import AppState
from polymarket_watch.state import default_state
from services.ingestion.client import IngestionClient
from services.ingestion.worker import insert_trades, upsert_markets

logger = logging.getLogger(__name__)

STATE_KEY = "backfill:last_market"


def load_resume_key(session) -> str | None:
    row = session.execute(select(AppState).where(AppState.key == STATE_KEY)).scalar_one_or_none()
    return row.value if row else None


def store_resume_key(session, value: str) -> None:
    session.execute(
        AppState.__table__.insert()
        .values(key=STATE_KEY, value=value)
        .on_conflict_do_update(index_elements=[AppState.key], set_={"value": value})
    )


def backfill(markets_limit: int, days: int, concurrency: int) -> None:
    setup_logging()
    cfg = settings
    state = default_state()
    client = IngestionClient(cfg)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    markets = client.fetch_markets()
    if markets_limit:
        markets = markets[:markets_limit]

    resume_from: str | None
    with state.session_factory() as session:
        with session.begin():
            upsert_markets(session, markets)
        resume_from = load_resume_key(session)

    markets_to_process = markets if resume_from is None else [m for m in markets if m["external_id"] >= resume_from]
    logger.info("Starting backfill", extra={"markets": len(markets_to_process), "cutoff": cutoff.isoformat()})

    def fetch_with_retry(market_id: str, attempts: int = 3, delay: float = 1.0) -> list[dict[str, Any]]:
        for i in range(attempts):
            try:
                return client.fetch_recent_trades(market_id, since_ts=cutoff)
            except Exception as exc:
                sleep_for = delay * (2**i)
                logger.warning(
                    "fetch failed, retrying",
                    extra={"market": market_id, "attempt": i + 1, "sleep": sleep_for, "error": str(exc)},
                )
                time.sleep(sleep_for)
        return []

    def process_market(market: dict[str, Any]) -> None:
        market_id = market["external_id"]
        trades = fetch_with_retry(market_id)
        with state.session_factory() as session:
            with session.begin():
                snapshot = upsert_markets(session, [market])
                insert_trades(session, trades, snapshot)
                store_resume_key(session, market_id)
        logger.info("backfilled market", extra={"market": market_id, "trades": len(trades)})

    for idx, market in enumerate(markets_to_process):
        process_market(market)
        if (idx + 1) % max(concurrency, 1) == 0:
            time.sleep(0.2)

    client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill trades")
    parser.add_argument("--days", type=int, default=30, help="Days of history to fetch")
    parser.add_argument("--market-limit", type=int, default=200, help="Max markets to backfill")
    parser.add_argument("--concurrency", type=int, default=10, help="Batch size for pacing (polite delay)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    backfill(markets_limit=args.market_limit, days=args.days, concurrency=args.concurrency)


if __name__ == "__main__":
    main()
