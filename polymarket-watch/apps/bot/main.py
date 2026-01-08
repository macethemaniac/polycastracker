from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    Updater,
    CallbackContext,
)

from polymarket_watch.config import settings
from polymarket_watch.logging import setup_logging
from polymarket_watch.models import Alert, Market, SignalEvent, Trade
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
    await update.message.reply_text(
        "polymarket-watch bot is live.\n"
        "Commands:\n"
        "- /ping\n"
        "- /status\n"
        "- /top\n"
        "- /alert <id>\n"
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
            "status:\n"
            f"- markets: {markets}\n"
            f"- trades: {trades} (last: {_fmt_dt(last_trade)})\n"
            f"- signals: {signals} (last: {_fmt_dt(last_signal)})\n"
            f"- alerts: {alerts} (last: {_fmt_dt(last_alert)})"
        )

    text = await asyncio.to_thread(_query)
    await update.message.reply_text(text)


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    session_factory = context.bot_data.get("session_factory")
    if not session_factory:
        await update.message.reply_text("session unavailable")
        return

    def _query() -> str:
        with session_factory() as session:
            rows = (
                session.execute(
                    select(Alert, Market)
                    .join(Market, Alert.market_id == Market.id, isouter=True)
                    .order_by(Alert.score.desc().nullslast(), Alert.updated_at.desc())
                    .limit(5)
                )
                .all()
            )
        if not rows:
            return "No alerts found."
        lines = ["top alerts:"]
        for alert, market in rows:
            title = market.name if market else f"market {alert.market_id}"
            side = alert.side or "n/a"
            score = f"{float(alert.score):.2f}" if alert.score is not None else "n/a"
            lines.append(f"- [{alert.id}] {title} | side={side} | score={score}")
        return "\n".join(lines)

    try:
        text = await asyncio.to_thread(_query)
    except Exception as exc:  # pragma: no cover - defensive
        logging.exception("top command failed", exc_info=exc)
        await update.message.reply_text("Error reading alerts; check bot logs.")
        return
    await update.message.reply_text(text)


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


def build_application() -> Application:
    setup_logging()
    token = settings.telegram_bot_token
    if not token or token == "CHANGEME":
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    state = default_state()
    application = Application.builder().token(token).build()
    application.bot_data["session_factory"] = state.session_factory

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("top", top))
    application.add_handler(CommandHandler("alert", alert))
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
