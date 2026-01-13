from sqlalchemy import text
from polymarket_watch.db import engine

def check_and_fix():
    with engine.connect() as conn:
        # Check existing constraints
        result = conn.execute(text("""
            SELECT conname FROM pg_constraint 
            WHERE conrelid = 'alerts'::regclass AND contype = 'u'
        """))
        constraints = [row[0] for row in result]
        print(f"Existing unique constraints: {constraints}")
        
        # Drop old constraint if it exists
        if 'uq_alerts_market_side_event' in constraints:
            print("Dropping old constraint...")
            conn.execute(text("ALTER TABLE alerts DROP CONSTRAINT uq_alerts_market_side_event"))
            conn.commit()
            print("Old constraint dropped.")
        
        # Add new constraint if not exists
        if 'uq_alerts_market_side_event_wallet' not in constraints:
            print("Adding new constraint...")
            try:
                conn.execute(text("""
                    ALTER TABLE alerts 
                    ADD CONSTRAINT uq_alerts_market_side_event_wallet 
                    UNIQUE (market_id, side, event_type, wallet_address)
                """))
                conn.commit()
                print("New constraint added.")
            except Exception as e:
                print(f"Constraint error (may already exist): {e}")
        else:
            print("New constraint already exists.")

if __name__ == "__main__":
    check_and_fix()
