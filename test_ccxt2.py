import ccxt

b = ccxt.binance({'options': {'defaultType': 'future'}})
try:
    print("Loading markets...")
    b.load_markets()
    print("Fetching MET/USDT...")
    res = b.fetch_ohlcv('MET/USDT', '1h')
    print(f"Success! Fetched {len(res)} candles")
except Exception as e:
    print("Exception class:", type(e).__name__)
    print(e)
