from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from polymarket_watch.config import settings
from polymarket_watch.logging import setup_logging
from polymarket_watch.models import Trade, WalletStats
from polymarket_watch.state import default_state
from .accuracy import WalletAccuracyScorer

logger = logging.getLogger(__name__)

IDLE_SLEEP_SECONDS = 300
BATCH_SIZE = 100


def process_backfill(session: Session, scorer: WalletAccuracyScorer) -> int:
    """Evaluate recent trades that haven't been scored yet."""
    # This is a simplified backfill for the MVP.
    # In a production system, we'd track 'last_processed_trade_id'.
    # For now, evaluate all trades in the last 24h.
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    trades = session.execute(
        select(Trade)
        .where(Trade.traded_at >= cutoff)
        .order_by(Trade.traded_at.desc())
        .limit(BATCH_SIZE)
    ).scalars().all()
    
    if not trades:
        return 0
        
    outcomes = []
    for t in trades:
        outcome = scorer.evaluate_trade(session, t)
        if outcome:
            outcomes.append(outcome)
            
    if outcomes:
        updated = scorer.update_wallet_stats(session, outcomes)
        return updated
    return 0


def run_worker() -> None:
    cfg = settings
    setup_logging(cfg)
    state = default_state()
    scorer = WalletAccuracyScorer()
    
    logger.info("Profiling worker started (Wallet Accuracy)")
    
    while True:
        try:
            with state.session_factory() as session:
                with session.begin():
                    processed = process_backfill(session, scorer)
                    if processed > 0:
                        logger.info("Profiling iteration", extra={"updated_wallets": processed})
            
            time.sleep(IDLE_SLEEP_SECONDS)
        except Exception:
            logger.exception("Profiling worker error")
            time.sleep(60)


def main() -> None:
    run_worker()


if __name__ == "__main__":
    main()
