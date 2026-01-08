import asyncio
import io
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
from sqlalchemy import func, select
from telegram import Bot

from polymarket_watch.config import settings
from polymarket_watch.logging import setup_logging
from polymarket_watch.models import Market, SignalEvent, Trade, WalletProfile, WalletStats
from polymarket_watch.state import default_state

logger = logging.getLogger(__name__)

async def generate_weekly_report(session, bot: Bot, chat_id: str):
    logger.info("Generating weekly report...")
    
    # 1. Time Window (Last 7 Days)
    now = datetime.now(timezone.utc)
    one_week_ago = now - timedelta(days=7)
    
    # 2. Fetch Active Wallets
    # We look for wallets active in the last week
    active_wallets_stmt = (
        select(Trade.wallet_address, func.sum(Trade.shares * Trade.price).label("volume"))
        .where(Trade.traded_at >= one_week_ago)
        .group_by(Trade.wallet_address)
        .order_by(func.sum(Trade.shares * Trade.price).desc())
        .limit(50)
    )
    active_wallets = session.execute(active_wallets_stmt).all()
    
    data = []
    
    for wallet_address, volume in active_wallets:
        # Fetch Wallet Stats for Accuracy
        stats = session.execute(
            select(WalletStats).where(WalletStats.wallet_address == wallet_address)
        ).scalar_one_or_none()
        
        # Estimate PnL (Very rough proxy: Realized on resolved markets + paper gains)
        # For simplicity in this first version, we'll use:
        # PnL ~ (Accuracy * Volume * 0.5) if accurate, else -(Volume * 0.5) 
        # OR we can try to find their resolved trades.
        # Let's stick to WalletStats.avg_delta_when_correct as a proxy for profitability if available.
        # But WalletStats might be long-term.
        
        # Let's try to calculate simple PnL from Resolved Markets in the last week
        # Find trades by this wallet in markets resolved in the last week (or any market if resolved)
        
        pnl = Decimal("0")
        
        # Fetch trades for this wallet in the last week
        trades = session.execute(
            select(Trade, Market)
            .join(Market, Trade.market_id == Market.id)
            .where(Trade.wallet_address == wallet_address, Trade.traded_at >= one_week_ago)
        ).all()
        
        wins = 0
        total_trades = 0
        
        for trade, market in trades:
            total_trades += 1
            if market.resolved_at:
                # If resolved, did they win?
                # This logic depends on market outcome format (which we might not have fully parsed in Side)
                # Assuming 'Yes' pays $1 if outcome is YES.
                # We need to know the winning outcome. market.status doesn't give us the winner easily unless it's in the auxiliary data.
                # For now, let's skip realized PnL from resolution and use "Green Signals" as proxy.
                pass
                
        # Better PnL Proxy: Use WalletStats accuracy * volume
        # Ensure accuracy is Decimal
        raw_acc = stats.accuracy_score if stats else None
        accuracy = Decimal(str(raw_acc)) if raw_acc is not None else Decimal("0")
        
        # Ensure volume is Decimal
        vol_dec = Decimal(str(volume)) if volume is not None else Decimal("0")
        
        # Calculate PnL (all Decimals)
        est_pnl = vol_dec * (accuracy - Decimal("0.5")) * Decimal("2")
        
        profile = session.execute(select(WalletProfile).where(WalletProfile.wallet_address == wallet_address)).scalar_one_or_none()
        label = profile.label if profile else ""
        
        data.append({
            "Wallet": wallet_address,
            "Label": label,
            "Volume ($)": float(volume),
            "Winrate (%)": float(accuracy * 100),
            "Est. Weekly PnL ($)": float(est_pnl),
            "Total Trades": total_trades or (stats.total_trades if stats else 0)
        })
        
    if not data:
        logger.info("No data for weekly report.")
        await bot.send_message(chat_id=chat_id, text="Weekly Digest: No active wallets found with trades in the last 7 days.")
        return

    # Create DataFrame
    df = pd.DataFrame(data)
    df = df.sort_values("Est. Weekly PnL ($)", ascending=False)
    
    # Save to Excel
    file_buffer = io.BytesIO()
    with pd.ExcelWriter(file_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Weekly Top Wallets')
        
    file_buffer.seek(0)
    
    # Send
    await bot.send_document(
        chat_id=chat_id,
        document=file_buffer,
        filename=f"weekly_digest_{now.strftime('%Y-%m-%d')}.xlsx",
        caption=f" weekly digest: Top {len(df)} wallets by volume/pnl."
    )
    logger.info("Weekly report sent.")

def run_worker():
    cfg = settings
    setup_logging(cfg)
    logger.info("Starting reporting worker...")
    
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        logger.warning("Bot token or Chat ID missing. Reporting disabled.")
        return

    bot = Bot(token=cfg.telegram_bot_token)
    state = default_state()
    
    # Schedule: Run once immediately for testing if requested (or dev mode), then every Sunday
    # For now, simplistic loop
    
    while True:
        now = datetime.now()
        # Check if it is Sunday 23:xx
        # To make it robust without cron, we sleep.
        # Developer Request: "make sure it only sends weekly at the end of each week"
        
        # For testing, we might want to run it on startup if a flag is set? 
        # User said "implement... digest first". I'll assume standard loop.
        
        # If today is Sunday and time > 23:00 and we haven't run yet... 
        # A simpler way for a persistent process is using `schedule` lib, but I'll stick to simple check.
        
        # Wait for next hour check
        time.sleep(60) 

def main():
    asyncio.run(run_worker_async())

async def run_worker_async():
    cfg = settings
    setup_logging(cfg)
    logger.info("Starting reporting service...")
    
    bot = Bot(token=cfg.telegram_bot_token)
    state = default_state()
    
    # Run once on startup for DEMONSTRATION? user asked "implement it", often implies "I want to see it work"
    # But strictly asked "sends weekly". 
    # I will add a command /digest to force it, and the worker will handle the schedule.
    # The worker logic here will be the schedule.
    
    last_run_week = None
    
    while True:
        now = datetime.now()
        current_week = now.isocalendar()[1]
        weekday = now.weekday() # 6 is Sunday
        
        # Run on Sunday at 23:00
        if weekday == 6 and now.hour >= 23 and last_run_week != current_week:
            try:
                with state.session_factory() as session:
                    await generate_weekly_report(session, bot, cfg.telegram_chat_id)
                last_run_week = current_week
            except Exception:
                logger.exception("Failed to generate report")
                
        await asyncio.sleep(600) # Check every 10 mins

if __name__ == "__main__":
    main()
