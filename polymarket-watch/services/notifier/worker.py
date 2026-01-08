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
from polymarket_watch.models import Alert, AppState, Market, SignalEvent, WalletProfile, WalletStats
from polymarket_watch.state import default_state

from telegram import Bot, constants, InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

CURSOR_KEY = "cursor:notifier:last_alert_ts"
IDLE_SLEEP_SECONDS = 15
BACKOFF_BASE_SECONDS = 5
BACKOFF_MAX_SECONDS = 300
REASONS_LIMIT = 3
WALLETS_LIMIT = 3  # Only show top 3 wallets per alert



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


def _build_message(
    alert: Alert,
    market: Optional[Market],
    signals_data: list[tuple[SignalEvent, Optional[WalletProfile], Optional[WalletStats]]],
) -> str:
    # Header: Type - Market Name (Hyperlink)
    # "Depending on the type label wallet kind and reason as the header eg :Low or high activity market"
    # We will use the alert event_type and market name.
    
    market_name = market.name if market else f"Market {alert.market_id}"
    market_url = f"https://polymarket.com/market/{market.external_id}" if market and market.external_id else "https://polymarket.com/"
    
    # Kind of market (using alert event type or reasoning)
    market_kind = alert.event_type.replace("_", " ").title()
    
    # Outcome (Yes/No) - extracted from Signal or Alert side
    outcome = (alert.side or "n/a").upper()
    
    header_link = f'<a href="{market_url}">{market_kind} - {market_name}</a>'
    
    lines = [
        header_link,
        f"<b>{market_kind}</b>",
        f"Outcome: {outcome}",
        "", # Empty line for spacing
    ]
    
    if not signals_data:
        lines.append("No specific trader details available.")
        return "\n".join(lines)
        
    for signal, profile, stats in signals_data:
        # Trader Name (Hyperlink)
        trader_name = profile.label if profile and profile.label else (signal.wallet_address[:6] + "..." if signal.wallet_address else "Unknown")
        trader_url = f"https://polymarket.com/profile/{signal.wallet_address}" if signal.wallet_address else "#"
        trader_link = f'<a href="{trader_url}">{trader_name}</a>'
        
        # Side
        trade_side = (signal.side or outcome).upper()
        
        # Trade: Shares @ Price
        details = signal.details_json or {}
        shares = details.get("shares") or details.get("amount") or 0
        try:
             shares_val = float(shares)
             shares_str = f"{shares_val:,.0f}"
        except (ValueError, TypeError):
             shares_str = str(shares)
             
        price = details.get("price") or 0
        try:
            price_val = float(price)
            price_str = f"{price_val:.2f}¢" if price_val < 1 else f"${price_val:.2f}"
            # Polymarket prices are often 0.xx, representing cents. Let's assume standard formatting.
            if price_val < 1.0:
                 price_str = f"{int(price_val * 100)}¢"
        except (ValueError, TypeError):
            price_str = str(price)
            
        trade_info = f"{shares_str} @ {price_str}"
        
        # Notional
        notional = details.get("notional") or details.get("total_notional") or 0
        try:
            notional_val = float(notional)
            notional_str = f"${notional_val:,.2f}"
        except (ValueError, TypeError):
             notional_str = str(notional)

        # Unique markets lifetime (using total_trades as proxy or placeholder if not available)
        # The user asked for "unique markets lifetime". WalletStats has total_trades. 
        # We don't have "unique markets" count in WalletStats easily, so we'll use Total Trades for now 
        # or mock it if strictly required, but let's stick to what we have.
        lifetime_trades = stats.total_trades if stats else "n/a"
        
        # Winrate
        # Accuracy score is 0.0-1.0. Convert to %
        if stats and stats.accuracy_score is not None:
            winrate = f"{float(stats.accuracy_score) * 100:.1f}%"
        else:
            winrate = "n/a"

        # Format:
        # Trader | Side | Trade
        # Notional | Unique Markets | Winrate
        
        lines.append(f"Trader: {trader_link} | Side: {trade_side} | Trade: {trade_info}")
        lines.append(f"Notional: {notional_str} | Lifetime Trades: {lifetime_trades} | Winrate: {winrate}")
        lines.append("") # Spacing between wallets
        
    return "\n".join(lines)


async def _send(bot: Bot, chat_id: str, text: str, reply_markup=None, dry_run: bool=False) -> None:
    if dry_run:
        logger.info("DRY-RUN notifier message", extra={"chat_id": chat_id, "text": text})
        return
    await bot.send_message(
        chat_id=chat_id, 
        text=text, 
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=False,
        reply_markup=reply_markup
    )


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
                                select(SignalEvent, WalletProfile, WalletStats)
                                .outerjoin(WalletProfile, SignalEvent.wallet_profile_id == WalletProfile.id)
                                .outerjoin(WalletStats, SignalEvent.wallet_address == WalletStats.wallet_address)
                                .where(
                                    SignalEvent.market_id == alert.market_id,
                                    SignalEvent.side == alert.side,
                                )
                                .order_by(SignalEvent.observed_at.desc(), SignalEvent.created_at.desc())
                                .limit(WALLETS_LIMIT)
                            )
                            .all()
                        )
                        message = _build_message(alert, market, signal_rows)
                        
                        # Build Buttons for Wallets
                        # We want a button for each wallet in signal_rows (unique)
                        unique_wallets = {}
                        for sig, prof, _ in signal_rows:
                            if sig.wallet_address and sig.wallet_address not in unique_wallets:
                                # Determine label (profile label or truncated address)
                                label = prof.label if prof and prof.label else f"{sig.wallet_address[:6]}..."
                                # Check if already watched? We need the profile object.
                                is_watched = prof.is_watched if prof else False
                                # Callback data: "track:<address>" or "untrack:<address>"
                                action = "untrack" if is_watched else "track"
                                btn_text = f"{'Untrack' if is_watched else 'Track'} {label}"
                                unique_wallets[sig.wallet_address] = (action, btn_text)

                        reply_markup = None
                        if unique_wallets:
                            keyboard = []
                            # Rows of 2 buttons
                            row = []
                            for addr, (action, btn_text) in unique_wallets.items():
                                row.append(InlineKeyboardButton(btn_text, callback_data=f"{action}:{addr}"))
                                if len(row) == 2:
                                    keyboard.append(row)
                                    row = []
                            if row:
                                keyboard.append(row)
                            reply_markup = InlineKeyboardMarkup(keyboard)

                        await _send(bot, chat_id, message, reply_markup=reply_markup, dry_run=cfg.notifier_dry_run or not cfg.telegram_chat_id)

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
