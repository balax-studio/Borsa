import ccxt
b = ccxt.binance({'options': {'defaultType': 'future'}})
b.load_markets()
try:
    b.fetch_ohlcv("MET/USDT", "1h") # Note: passing spot symbol to future client
except Exception as e:
    print("Class:", type(e).__name__)
    print("Msg:", e)
