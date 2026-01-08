import httpx

def test_clob():
    url = "https://clob.polymarket.com/markets"
    print(f"Testing CLOB: {url}")
    resp = httpx.get(url)
    data = resp.json()
    # CLOB returns a list of objects with 'question'
    if isinstance(data, list):
        print(f"Count: {len(data)}")
        for m in data[:5]:
            print(f" - {m.get('question')} (ID: {m.get('condition_id')})")
    elif isinstance(data, dict):
         print(f"Keys: {data.keys()}")

if __name__ == "__main__":
    test_clob()
