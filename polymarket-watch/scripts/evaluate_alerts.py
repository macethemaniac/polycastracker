from __future__ import annotations

import argparse
import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from polymarket_watch.logging import setup_logging
from polymarket_watch.models import Alert, BacktestResult, Trade
from polymarket_watch.state import default_state

logger = logging.getLogger(__name__)


def _price_at(session, market_id: int, ts) -> Decimal | None:
    row = (
        session.execute(
            select(Trade.price)
            .where(Trade.market_id == market_id, Trade.traded_at <= ts)
            .order_by(Trade.traded_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    return Decimal(row) if row is not None else None


def compute_results() -> int:
    setup_logging()
    state = default_state()
    total = 0
    with state.session_factory() as session:
        with session.begin():
            alerts = session.execute(select(Alert).order_by(Alert.created_at)).scalars().all()
            for alert in alerts:
                if not alert.market_id or not alert.created_at:
                    continue
                t0 = alert.created_at
                prices = {
                    "price_t0": _price_at(session, alert.market_id, t0),
                    "price_15m": _price_at(session, alert.market_id, t0 + timedelta(minutes=15)),
                    "price_1h": _price_at(session, alert.market_id, t0 + timedelta(hours=1)),
                    "price_4h": _price_at(session, alert.market_id, t0 + timedelta(hours=4)),
                }
                deltas = {}
                for key in ("15m", "1h", "4h"):
                    p0 = prices["price_t0"]
                    pn = prices[f"price_{key}"]
                    deltas[f"delta_{key}"] = (pn - p0) if p0 is not None and pn is not None else None

                stmt = insert(BacktestResult).values(
                    alert_id=alert.id,
                    market_id=alert.market_id,
                    side=alert.side,
                    score=alert.score,
                    alert_time=alert.created_at,
                    **prices,
                    delta_15m=deltas.get("delta_15m"),
                    delta_1h=deltas.get("delta_1h"),
                    delta_4h=deltas.get("delta_4h"),
                ).on_conflict_do_update(
                    index_elements=[BacktestResult.alert_id],
                    set_={
                        "price_t0": stmt.excluded.price_t0,
                        "price_15m": stmt.excluded.price_15m,
                        "price_1h": stmt.excluded.price_1h,
                        "price_4h": stmt.excluded.price_4h,
                        "delta_15m": stmt.excluded.delta_15m,
                        "delta_1h": stmt.excluded.delta_1h,
                        "delta_4h": stmt.excluded.delta_4h,
                    },
                )
                session.execute(stmt)
                total += 1
    logger.info("computed backtest results", extra={"alerts": total})
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate alerts with price deltas")
    parser.parse_args()
    compute_results()


if __name__ == "__main__":
    main()
