from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import logging
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Updater,
    CallbackContext,
)

from polymarket_watch.config import settings
from polymarket_watch.logging import setup_logging
from polymarket_watch.models import Alert, Market, SignalEvent, Trade, WalletProfile, WalletStats
# Import generate_weekly_report to run it on command
from services.reporting.worker import generate_weekly_report

from polymarket_watch.state import default_state
from sqlalchemy import func, select

# Work around python-telegram-bot Updater __slots__ bug on Python 3.14 by subclassing and overriding references.
try:
    from telegram.ext import _updater as _updater_module
    from telegram.ext import _applicationbuilder as _app_builder_module

    class PatchedUpdater(_updater_module.Updater):  # type: ignore[misc]
        __slots__ = tuple(getattr(_updater_module.Updater, "__slots__", ())) + (
            "__polling_cleanup_cb",
            "_Updater__polling_cleanup_cb",
            "__dict__",
        )

        def __init__(self, bot, update_queue):
            super().__init__(bot=bot, update_queue=update_queue)
            # Ensure attributes exist even if base __init__ changes
            self._Updater__polling_cleanup_cb = None
            self.__polling_cleanup_cb = None

    _updater_module.Updater = PatchedUpdater
    _app_builder_module.Updater = PatchedUpdater
    Updater = PatchedUpdater  # type: ignore[assignment]
except Exception:
    pass


def _fmt_dt(dt: datetime | None) -> str:
    return dt.isoformat() if dt else "n/a"


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("pong")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_html(
        "<b>🚀 Polycast Tracker is Live!</b>\n\n"
        "Welcome to the ultimate Polymarket intelligence hub. I'm monitoring the whales and smart wallets so you don't have to.\n\n"
        "<b>🛠 Available Commands:</b>\n"
        "🔹 /digest - 📊 Get your weekly alpha (Excel report)\n"
        "🔹 /top - 🔥 View current hottest alerts\n"
        "🔹 /status - 📈 Check system health & stats\n"
        "🔹 /alert [id] - 🔍 Deep dive into a specific alert\n"
        "🔹 /ping - 🏓 Quick heartbeat check\n\n"
        "<i>Stay ahead of the market. Good luck!</i>"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    session_factory = context.bot_data.get("session_factory")
    if not session_factory:
        await update.message.reply_text("session unavailable")
        return

    def _query() -> str:
        with session_factory() as session:
            markets = session.execute(select(func.count(Market.id))).scalar_one()
            trades = session.execute(select(func.count(Trade.id))).scalar_one()
            signals = session.execute(select(func.count(SignalEvent.id))).scalar_one()
            alerts = session.execute(select(func.count(Alert.id))).scalar_one()
            last_trade = session.execute(select(func.max(Trade.traded_at))).scalar_one()
            last_signal = session.execute(select(func.max(SignalEvent.observed_at))).scalar_one()
            last_alert = session.execute(select(func.max(Alert.updated_at))).scalar_one()
        return (
            "<b>📊 System Status Report</b>\n\n"
            f"🏛 <b>Markets Tracked:</b> {markets}\n"
            f"🤝 <b>Total Trades:</b> {trades}\n"
            f"⚡️ <b>Signals Found:</b> {signals}\n"
            f"🔔 <b>Alerts Sent:</b> {alerts}\n\n"
            "<b>🕒 Last Activity:</b>\n"
            f"🔹 Trade: <code>{_fmt_dt(last_trade)}</code>\n"
            f"🔹 Signal: <code>{_fmt_dt(last_signal)}</code>\n"
            f"🔹 Alert: <code>{_fmt_dt(last_alert)}</code>\n\n"
            "🟢 <i>All systems operational.</i>"
        )

    text = await asyncio.to_thread(_query)
    await update.message.reply_html(text)


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    session_factory = context.bot_data.get("session_factory")
    if not session_factory:
        await update.message.reply_text("session unavailable")
        return

    def _query() -> str:
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            # Filter for Dec 2025 activity onward (and recent alerts last 7 days)
            start_of_relevance = datetime(2025, 12, 1, tzinfo=timezone.utc)
            recent_alert_cutoff = now - timedelta(days=7)

            # 1. Identify trending markets (active, new, high density)
            trending_sub = (
                select(Alert.market_id, func.count(Alert.id).label("alert_density"))
                .join(Market, Alert.market_id == Market.id)
                .where(
                    Market.status == "active",
                    Market.created_at >= datetime(2025, 1, 1, tzinfo=timezone.utc), # 2025+ markets
                    Alert.updated_at >= recent_alert_cutoff # Recent alerts only
                )
                .group_by(Alert.market_id)
                .subquery()
            )

            # 2. Main query for alerts from newer trending markets with whales
            stmt = (
                select(
                    Alert,
                    Market,
                    func.max(WalletStats.accuracy_score).label("max_whale_acc"),
                    trending_sub.c.alert_density
                )
                .join(Market, Alert.market_id == Market.id)
                .join(trending_sub, Market.id == trending_sub.c.market_id)
                .join(SignalEvent, SignalEvent.market_id == Market.id, isouter=True)
                .join(WalletStats, SignalEvent.wallet_address == WalletStats.wallet_address, isouter=True)
                # Ensure the signals we consider are also recent
                .where(SignalEvent.observed_at >= start_of_relevance) 
                .group_by(Alert.id, Market.id, trending_sub.c.alert_density)
                .order_by(
                    # Primary: Smart Whale participation (Accuracy >= 0.6)
                    (func.max(WalletStats.accuracy_score) >= 0.6).desc().nullslast(),
                    # Secondary: Trending density
                    trending_sub.c.alert_density.desc(),
                    # Tertiary: Newness (Market ID)
                    Market.id.desc(),
                    # Quaternary: Score
                    Alert.score.desc()
                )
                .limit(5)
            )
            rows = session.execute(stmt).all()

        if not rows:
            return "No active 2026 alpha found yet. Monitoring trending markets..."

        lines = ["<b>🔥 Top Alpha Alerts (Trending & Whales)</b>\n"]
        for alert, market, max_acc, density in rows:
            title = market.name if market else f"Market {alert.market_id}"
            score = f"{float(alert.score or 0):.1f}"
            acc_str = f"🐋 <b>{float(max_acc or 0)*100:.0f}% Whale</b>" if max_acc and max_acc >= 0.6 else "📈 Trending"
            hot_lvl = "🔥" * min(3, int(density or 1))
            
            market_url = f"https://polymarket.com/market/{market.external_id}" if market and market.external_id else "https://polymarket.com/"
            
            lines.append(
                f"{hot_lvl} <b>{title}</b>\n"
                f"   └ <code>ID:{alert.id}</code> | Score: {score} | {acc_str}\n"
                f"   └ <a href=\"{market_url}\">🔗 Trade Now</a>\n"
            )
        return "\n".join(lines)

    try:
        text = await asyncio.to_thread(_query)
    except Exception as exc:
        logging.exception("top command failed", exc_info=exc)
        await update.message.reply_text("Error reading alerts; check bot logs.")
        return
    await update.message.reply_html(text)


async def alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    session_factory = context.bot_data.get("session_factory")
    if not session_factory:
        await update.message.reply_text("session unavailable")
        return
    if not context.args:
        await update.message.reply_text("Usage: /alert <id>")
        return
    try:
        alert_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Alert id must be an integer.")
        return

    def _query() -> str:
        with session_factory() as session:
            row = (
                session.execute(
                    select(Alert, Market)
                    .join(Market, Alert.market_id == Market.id, isouter=True)
                    .where(Alert.id == alert_id)
                )
                .one_or_none()
            )
            if not row:
                return f"Alert {alert_id} not found."
            alert_obj, market = row
            reasons: list[str] = []
            why: dict[str, Any] = alert_obj.why_json or {}
            counts = why.get("counts_by_signal", {}) if isinstance(why, dict) else {}
            for sig, count in list(counts.items())[:3]:
                reasons.append(f"{sig} x{count}")
            examples = why.get("examples", []) if isinstance(why, dict) else []
            wallet_snippets: list[str] = []
            for ex in examples[:3]:
                wallet = ex.get("wallet") or "wallet?"
                side = ex.get("side") or "n/a"
                sev = ex.get("severity") or ""
                ts = ex.get("observed_at") or "n/a"
                wallet_snippets.append(f"{wallet} side={side} {sev} at {ts}")
            title = market.name if market else f"market {alert_obj.market_id}"
            score = f"{float(alert_obj.score):.2f}" if alert_obj.score is not None else "n/a"
            lines = [
                f"Alert {alert_obj.id}",
                f"Title: {title}",
                f"Side: {alert_obj.side or 'n/a'} | Status: {alert_obj.status or 'n/a'} | Score: {score}",
            ]
            if reasons:
                lines.append("Reasons: " + "; ".join(reasons))
            if wallet_snippets:
                lines.append("Examples: " + "; ".join(wallet_snippets))
            return "\n".join(lines)

    text = await asyncio.to_thread(_query)
    await update.message.reply_text(text)


async def digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    session_factory = context.bot_data.get("session_factory")
    if not session_factory:
        await update.message.reply_text("session unavailable")
        return
        
    await update.message.reply_text("Generating weekly digest... check back in a moment.")
    
    # We call the generation logic directly
    # Note: generate_weekly_report expects (session, bot, chat_id).
    # We need to construct a session or pass the factory? 
    # The worker used `with state.session_factory() as session`.
    # Here we should probably do the same.
    
    try:
        # We need to import the function first. I will add the import at top of file.
        # But wait, services.reporting might not be a package yet if I didn't add __init__.
        # I need to ensure __init__.py exists in services/reporting.
        
        # Using a fresh session for the report generation
        with session_factory() as session:
             # We use the chat_id from the update to send it back to the requester
             await generate_weekly_report(session, context.bot, update.effective_chat.id)
             
    except Exception as exc:
        logging.exception("Digest generation failed", exc_info=exc)
        await update.message.reply_text("Failed to generate digest.")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not query.data:
        return

    # Data format: "action:payload"
    try:
        action, payload = query.data.split(":", 1)
    except ValueError:
        return

    if action in ("track", "untrack"):
        wallet_address = payload
        should_track = (action == "track")
        
        session_factory = context.bot_data.get("session_factory")
        if not session_factory:
            await query.edit_message_text("Session unavailable.")
            return

        with session_factory() as session:
            with session.begin():
                stmt = select(WalletProfile).where(WalletProfile.wallet_address == wallet_address)
                profile = session.execute(stmt).scalar_one_or_none()
                
                if not profile:
                    # Create if not exists (should theoretically exist if mentioned in signal, but maybe signal from unknown wallet)
                    # Although our system creates profiles on ingestion usually? 
                    # Actually, ingestion might use raw addresses. 
                    if should_track:
                        profile = WalletProfile(wallet_address=wallet_address, is_watched=True)
                        session.add(profile)
                        msg = f"Created and tracking {wallet_address}"
                    else:
                         msg = f"Wallet {wallet_address} not found."
                else:
                    profile.is_watched = should_track
                    session.add(profile)
                    msg = f"{'Now tracking' if should_track else 'Stopped tracking'} {profile.label or wallet_address}"
        
        await query.message.reply_text(msg)
    else:
        await query.message.reply_text(f"Unknown action: {action}")


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "🚀 Start the bot & see instructions"),
        BotCommand("digest", "📊 Get weekly alpha Excel report"),
        BotCommand("top", "🔥 View current hottest alerts"),
        BotCommand("status", "📈 Check system health & stats"),
        BotCommand("alert", "🔍 Deep dive into alert by ID"),
        BotCommand("help", "❓ Show help information"),
        BotCommand("ping", "🏓 Quick heartbeat check"),
    ])


def build_application() -> Application:
    setup_logging()
    token = settings.telegram_bot_token
    if not token or token == "CHANGEME":
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    state = default_state()
    application = Application.builder().token(token).post_init(post_init).build()
    application.bot_data["session_factory"] = state.session_factory

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("top", top))
    application.add_handler(CommandHandler("alert", alert))
    application.add_handler(CommandHandler("digest", digest))
    application.add_handler(CallbackQueryHandler(on_callback))
    return application


def main() -> None:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = build_application()
    async def on_error(update: object, context: CallbackContext) -> None:
        logging.exception("Bot error", exc_info=context.error)
        if isinstance(update, Update) and update.message:
            try:
                await update.message.reply_text("Bot error occurred; check logs.")
            except Exception:
                pass

    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
