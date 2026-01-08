from polymarket_watch.state import default_state
from polymarket_watch.models import Market, Trade, Alert, SignalEvent, WalletStats
from sqlalchemy import select, func

def check():
    state = default_state()
    with state.session_factory() as session:
        markets = session.execute(select(func.count(Market.id))).scalar()
        trades = session.execute(select(func.count(Trade.id))).scalar()
        signals = session.execute(select(func.count(SignalEvent.id))).scalar()
        alerts = session.execute(select(func.count(Alert.id))).scalar()
        wallet_stats = session.execute(select(func.count(WalletStats.wallet_address))).scalar()
        
        last_trade = session.execute(select(Trade.traded_at).order_by(Trade.traded_at.desc()).limit(1)).scalar()
        last_alert = session.execute(select(Alert.updated_at).order_by(Alert.updated_at.desc()).limit(1)).scalar()
        
        print(f"Stats:")
        print(f"- Markets: {markets}")
        print(f"- Trades: {trades}")
        print(f"- Signals: {signals}")
        print(f"- Alerts: {alerts}")
        print(f"- WalletStats: {wallet_stats}")
        if last_trade:
            print(f"- Last Trade At: {last_trade}")
        else:
            print(f"- No trades found.")
        if last_alert:
            print(f"- Last Alert At: {last_alert}")
        else:
            print(f"- No alerts found.")

if __name__ == "__main__":
    check()
