from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymarket_watch.config import settings
from polymarket_watch.logging import setup_logging
from polymarket_watch.models import Alert, AppState, Market, SignalEvent
from polymarket_watch.state import default_state

from telegram import Bot

logger = logging.getLogger(__name__)

CURSOR_KEY = "cursor:notifier:last_alert_ts"
IDLE_SLEEP_SECONDS = 15
BACKOFF_BASE_SECONDS = 5
BACKOFF_MAX_SECONDS = 300
REASONS_LIMIT = 3
WALLETS_LIMIT = 3


def _load_cursor(session: Session) -> datetime | None:
    row = session.execute(select(AppState).where(AppState.key == CURSOR_KEY)).scalar_one_or_none()
    if row and row.value:
        try:
            dt = datetime.fromisoformat(row.value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _store_cursor(session: Session, value: datetime) -> None:
    iso = value.isoformat()
    stmt = insert(AppState).values(key=CURSOR_KEY, value=iso)
    stmt = stmt.on_conflict_do_update(index_elements=[AppState.key], set_={"value": iso})
    session.execute(stmt)


def _format_reasons(why_json: dict) -> list[str]:
    counts = why_json.get("counts_by_signal", {}) if isinstance(why_json, dict) else {}
    sorted_reasons = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    lines = []
    for sig, count in sorted_reasons[:REASONS_LIMIT]:
        lines.append(f"{sig} x{count}")
    return lines


def _format_wallets(signals: Iterable[SignalEvent]) -> list[str]:
    lines: list[str] = []
    for s in signals:
        details = s.details_json or {}
        notional = details.get("notional") or details.get("total_notional") or "n/a"
        price = details.get("price") or "n/a"
        shares = details.get("shares") or details.get("amount") or "n/a"
        ts = (s.observed_at or s.created_at).isoformat() if (s.observed_at or s.created_at) else "n/a"
        wallet = s.wallet_address or "wallet?"
        lines.append(f"{wallet} size={shares}@{price} notional={notional} at {ts}")
    return lines


def _build_message(alert: Alert, market: Optional[Market], signals: list[SignalEvent]) -> str:
    title = market.name if market else f"market {alert.market_id}"
    side = alert.side or "n/a"
    score = f"{float(alert.score):.2f}" if alert.score is not None else "n/a"
    reasons = _format_reasons(alert.why_json or {})
    wallets = _format_wallets(signals[:WALLETS_LIMIT])
    link = f"https://polymarket.com/market/{market.external_id}" if market and market.external_id else ""

    parts = [
        f"ALERT [{alert.status or 'watch'}] {title}",
        f"side={side} score={score}",
    ]
    if link:
        parts.append(f"link: {link}")
    if reasons:
        parts.append("reasons: " + " | ".join(reasons))
    if wallets:
        parts.append("wallets: " + " | ".join(wallets))
    return "\n".join(parts)


async def _send(bot: Bot, chat_id: str, text: str, dry_run: bool) -> None:
    if dry_run:
        logger.info("DRY-RUN notifier message", extra={"chat_id": chat_id, "text": text})
        return
    await bot.send_message(chat_id=chat_id, text=text)


async def run_notifier() -> None:
    cfg = settings
    setup_logging(cfg)
    if not cfg.telegram_bot_token or cfg.telegram_bot_token == "CHANGEME":
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    if not cfg.telegram_chat_id:
        logger.warning("TELEGRAM_CHAT_ID not configured; running in dry-run mode")
    chat_id = cfg.telegram_chat_id or "dry-run"
    bot = Bot(token=cfg.telegram_bot_token)
    state = default_state()
    backoff_attempt = 0

    logger.info("Starting notifier worker", extra={"dry_run": cfg.notifier_dry_run})

    while True:
        try:
            with state.session_factory() as session:
                with session.begin():
                    cursor = _load_cursor(session)
                    stmt = (
                        select(Alert, Market)
                        .join(Market, Alert.market_id == Market.id, isouter=True)
                        .order_by(Alert.updated_at)
                        .limit(50)
                    )
                    if cursor:
                        stmt = stmt.where(Alert.updated_at > cursor)
                    rows = session.execute(stmt).all()
                    if not rows:
                        raise StopIteration

                    latest_ts = cursor
                    for alert, market in rows:
                        latest_ts = max(latest_ts, alert.updated_at) if latest_ts else alert.updated_at
                        if alert.status not in {"watch", "high"}:
                            continue
                        signal_rows = (
                            session.execute(
                                select(SignalEvent)
                                .where(
                                    SignalEvent.market_id == alert.market_id,
                                    SignalEvent.side == alert.side,
                                )
                                .order_by(SignalEvent.observed_at.desc(), SignalEvent.created_at.desc())
                                .limit(5)
                            )
                            .scalars()
                            .all()
                        )
                        message = _build_message(alert, market, signal_rows)
                        await _send(bot, chat_id, message, cfg.notifier_dry_run or not cfg.telegram_chat_id)

                    if latest_ts:
                        _store_cursor(session, latest_ts)
            backoff_attempt = 0
        except StopIteration:
            time.sleep(IDLE_SLEEP_SECONDS)
            continue
        except Exception:
            logger.exception("Notifier worker error")
            backoff = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2**backoff_attempt))
            backoff_attempt += 1
            time.sleep(backoff)


def main() -> None:
    asyncio.run(run_notifier())


if __name__ == "__main__":
    main()
