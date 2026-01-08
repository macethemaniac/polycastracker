import httpx

def test_trending():
    # Try different sort parameters
    urls = [
        "https://gamma-api.polymarket.com/events?active=true&closed=false&sortBy=volume24hr&ascending=false&limit=10",
        "https://gamma-api.polymarket.com/events?active=true&closed=false&order=last_trade_price_date&ascending=false&limit=10",
        "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=10&tag=Politics"
    ]
    
    for url in urls:
        print(f"\n--- Testing: {url} ---")
        try:
            resp = httpx.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for d in data[:5]:
                        print(f" - {d.get('title')}")
                else:
                    print(f"Response keys: {data.keys()}")
            else:
                print(f"Error {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"Exception: {e}")

if __name__ == "__main__":
    test_trending()
