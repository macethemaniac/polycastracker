from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymarket_watch.config import Settings, settings
from polymarket_watch.logging import setup_logging
from polymarket_watch.models import AppState as AppStateModel, Market, Trade
from polymarket_watch.state import AppState as ServiceState, default_state

from .client import IngestionClient

logger = logging.getLogger(__name__)

CURSOR_PREFIX = "cursor:trades:"


@dataclass
class MarketSnapshot:
    id: int
    external_id: str
    status: str
    resolved_at: datetime | None


def _active_market(snapshot: MarketSnapshot) -> bool:
    status = (snapshot.status or "").lower()
    return status not in {"resolved", "closed", "inactive"}


def upsert_markets(session: Session, markets: Iterable[dict[str, Any]]) -> dict[str, MarketSnapshot]:
    markets_list = list(markets)
    if not markets_list:
        return {}

    stmt = insert(Market).values(
        [
            {
                "external_id": m["external_id"],
                "name": m.get("name") or "",
                "category": m.get("category"),
                "status": m.get("status") or "active",
                "resolved_at": m.get("resolved_at"),
            }
            for m in markets_list
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Market.external_id],
        set_={
            "name": stmt.excluded.name,
            "category": stmt.excluded.category,
            "status": stmt.excluded.status,
            "resolved_at": stmt.excluded.resolved_at,
        },
    )
    session.execute(stmt)
    session.commit()

    external_ids = [m["external_id"] for m in markets_list]
    snapshots: dict[str, MarketSnapshot] = {}
    result = session.execute(select(Market).where(Market.external_id.in_(external_ids)))
    for row in result.scalars():
        snapshots[row.external_id] = MarketSnapshot(
            id=row.id, external_id=row.external_id, status=row.status, resolved_at=row.resolved_at
        )
    return snapshots


def _load_cursor(session: Session, market_external_id: str) -> datetime | None:
    key = f"{CURSOR_PREFIX}{market_external_id}"
    row = session.execute(select(AppStateModel).where(AppStateModel.key == key)).scalar_one_or_none()
    if row and row.value:
        try:
            dt = datetime.fromisoformat(row.value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _store_cursor(session: Session, market_external_id: str, value: datetime) -> None:
    key = f"{CURSOR_PREFIX}{market_external_id}"
    iso_value = value.isoformat()
    stmt = insert(AppStateModel).values(key=key, value=iso_value).on_conflict_do_update(
        index_elements=[AppStateModel.key],
        set_={"value": iso_value},
    )
    session.execute(stmt)
    session.commit()


def insert_trades(
    session: Session, trades: Iterable[dict[str, Any]], markets: Dict[str, MarketSnapshot]
) -> tuple[int, datetime | None]:
    trades_list = list(trades)
    if not trades_list:
        return 0, None

    values = []
    latest_at: datetime | None = None
    for trade in trades_list:
        market = markets.get(trade["market_external_id"])
        if not market:
            continue
        traded_at = trade.get("traded_at")
        if not traded_at:
            continue
        latest_at = traded_at if not latest_at or traded_at > latest_at else latest_at
        values.append(
            {
                "market_id": market.id,
                "wallet_profile_id": None,
                "wallet_address": trade["wallet_address"],
                "side": trade["side"],
                "shares": trade["shares"],
                "price": trade["price"],
                "traded_at": traded_at,
                "trade_hash": trade.get("trade_hash"),
            }
        )

    if not values:
        return 0, None

    stmt = insert(Trade).values(values).on_conflict_do_nothing()
    result = session.execute(stmt)
    session.commit()
    inserted = result.rowcount if hasattr(result, "rowcount") else 0
    return inserted or 0, latest_at


def refresh_markets(state: ServiceState, client: IngestionClient) -> dict[str, MarketSnapshot]:
    with state.session_factory() as session:
        markets = client.fetch_markets()
        snapshots = upsert_markets(session, markets)
        logger.info("Refreshed markets", extra={"count": len(snapshots)})
        return snapshots


def poll_market_trades(
    state: ServiceState, client: IngestionClient, market: MarketSnapshot
) -> tuple[int, datetime | None]:
    with state.session_factory() as session:
        since = _load_cursor(session, market.external_id)
        trades = client.fetch_recent_trades(market.external_id, since)
        inserted, latest_at = insert_trades(session, trades, {market.external_id: market})
        if latest_at:
            _store_cursor(session, market.external_id, latest_at)
        return inserted, latest_at


def _sleep_with_interval(min_seconds: int, max_seconds: int) -> None:
    if max_seconds <= min_seconds:
        time.sleep(min_seconds)
    else:
        time.sleep(random.uniform(min_seconds, max_seconds))


def run_worker(cfg: Settings, state: ServiceState, client: IngestionClient) -> None:
    setup_logging(cfg)
    logger.info("Starting ingestion worker")
    markets_cache: dict[str, MarketSnapshot] = {}
    poll_schedule: dict[str, float] = {}
    next_market_refresh_at = 0.0
    backoff_attempt = 0

    while True:
        try:
            now = time.time()
            if now >= next_market_refresh_at:
                markets_cache = refresh_markets(state, client)
                next_market_refresh_at = now + cfg.ingestion_markets_refresh_seconds
                poll_schedule = {
                    key: value for key, value in poll_schedule.items() if key in markets_cache
                }

            active_markets = [m for m in markets_cache.values() if _active_market(m)]
            if not active_markets:
                logger.debug("No active markets, sleeping")
                _sleep_with_interval(
                    cfg.ingestion_trades_poll_interval_min_seconds,
                    cfg.ingestion_trades_poll_interval_max_seconds,
                )
                continue

            for market in active_markets:
                due_at = poll_schedule.get(market.external_id, 0)
                if now < due_at:
                    continue
                try:
                    inserted, latest_at = poll_market_trades(state, client, market)
                    logger.info(
                        "Polled trades",
                        extra={
                            "market": market.external_id,
                            "inserted": inserted,
                            "latest_at": latest_at.isoformat() if latest_at else None,
                        },
                    )
                except Exception as e:
                    # Log and skip this market, continue with others
                    logger.warning(
                        "Failed to poll market trades",
                        extra={"market": market.external_id, "error": str(e)},
                    )
                poll_schedule[market.external_id] = time.time() + random.uniform(
                    cfg.ingestion_trades_poll_interval_min_seconds,
                    cfg.ingestion_trades_poll_interval_max_seconds,
                )

            backoff_attempt = 0
            time.sleep(1)
        except Exception:
            logger.exception("Ingestion loop error")
            backoff = min(
                cfg.ingestion_backoff_max_seconds,
                cfg.ingestion_backoff_base_seconds * (2**backoff_attempt),
            )
            backoff_attempt += 1
            time.sleep(backoff)


def main() -> None:
    cfg = settings
    state = default_state()
    client = IngestionClient(cfg)
    try:
        run_worker(cfg, state, client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
