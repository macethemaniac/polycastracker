from sqlalchemy import delete
from polymarket_watch.state import default_state
from polymarket_watch.models import Market, Trade, Alert, SignalEvent, WalletProfile, AppState as AppStateModel

def reset():
    state = default_state()
    with state.session_factory() as session:
        print("Cleaning up database...")
        session.execute(delete(Alert))
        session.execute(delete(SignalEvent))
        session.execute(delete(Trade))
        # Keep WalletProfile but maybe clear their stats? No, let's keep them.
        session.execute(delete(Market))
        session.execute(delete(AppStateModel)) # Clear cursors to force fresh sync
        session.commit()
        print("Database cleaned. Ready for fresh sync.")

if __name__ == "__main__":
    reset()
