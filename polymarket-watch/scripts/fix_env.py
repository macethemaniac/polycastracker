import os

def fix_env():
    env_path = ".env"
    if not os.path.exists(env_path):
        print(".env not found")
        return

    with open(env_path, "r") as f:
        lines = f.readlines()

    clean_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Remove corrupted parts or problematic overrides
        # We want to keep the bot token and IDs, but remove INGESTION overrides
        # that are clearly wrong or hardcoded to gamma.
        if line.startswith("INGESTION_MARKETS_URL") or line.startswith("INGESTION_TRADES_URL"):
            print(f"Removing override: {line}")
            continue
        
        if "i.polymarket.com/tr" in line:
            # Fix corrupted refresh seconds line
            key = line.split("=")[0]
            if key == "INGESTION_MARKETS_REFRESH_SECONDS":
                line = "INGESTION_MARKETS_REFRESH_SECONDS=600"
                print(f"Fixed: {line}")
        
        if "mma-api.polymarket.com/m" in line:
            # Fix corrupted backoff line
            key = line.split("=")[0]
            if key == "INGESTION_BACKOFF_BASE_SECONDS":
                line = "INGESTION_BACKOFF_BASE_SECONDS=5"
                print(f"Fixed: {line}")

        clean_lines.append(line)

    with open(env_path, "w") as f:
        f.write("\n".join(clean_lines) + "\n")
    print("Cleaned .env successfully.")

if __name__ == "__main__":
    fix_env()
