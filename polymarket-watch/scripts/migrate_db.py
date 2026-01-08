from sqlalchemy import text
from polymarket_watch.state import default_state

def migrate():
    state = default_state()
    with state.session_factory() as session:
        with session.begin():
            session.execute(text("ALTER TABLE wallet_profiles ADD COLUMN IF NOT EXISTS is_watched BOOLEAN DEFAULT false"))
            print("Added is_watched column")

if __name__ == "__main__":
    migrate()
