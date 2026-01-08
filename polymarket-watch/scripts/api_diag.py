import httpx
from polymarket_watch.config import settings

def diag():
    url = settings.ingestion_markets_url
    print(f"Fetching from {url}...")
    resp = httpx.get(url, headers={"User-Agent": "polymarket-watch/0.1"})
    print(f"Status: {resp.status_code}")
    payload = resp.json()
    
    if isinstance(payload, list):
        markets = payload
        next_cursor = None
    elif isinstance(payload, dict):
        markets = payload.get("markets", [])
        next_cursor = payload.get("next_cursor")
    else:
        markets = []
        next_cursor = None
        
    print(f"Total Count: {len(markets)}")
    
    for m in markets[:10]:
        print(f" - {m.get('question')} | Active: {m.get('active')} | Closed: {m.get('closed')} | ID: {m.get('condition_id')}")

if __name__ == "__main__":
    diag()
