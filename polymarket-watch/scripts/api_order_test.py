import httpx

def test_url(url):
    resp = httpx.get(url)
    data = resp.json()
    print(f"Data type: {type(data)}")
    if isinstance(data, dict):
        print(f"Keys: {data.keys()}")
        if "markets" in data:
            for m in data["markets"][:5]:
                print(f" - {m.get('question')}")
    elif isinstance(data, list):
        for m in data[:5]:
            print(f" - {m.get('question')}")

url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=5&order=last_trade_price_date&ascending=false"
test_url(url)
