from sqlalchemy import text
from polymarket_watch.db import engine

def migrate():
    with engine.connect() as conn:
        # Add wallet_address column if it doesn't exist
        try:
            conn.execute(text("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS wallet_address VARCHAR(128)"))
            conn.commit()
            print("Migration complete: wallet_address column added to alerts")
        except Exception as e:
            print(f"Migration error: {e}")
            # Try without IF NOT EXISTS for older postgres
            try:
                conn.execute(text("ALTER TABLE alerts ADD COLUMN wallet_address VARCHAR(128)"))
                conn.commit()
                print("Migration complete: wallet_address column added to alerts")
            except Exception as e2:
                if "already exists" in str(e2).lower():
                    print("Column already exists, skipping.")
                else:
                    print(f"Migration failed: {e2}")

if __name__ == "__main__":
    migrate()
