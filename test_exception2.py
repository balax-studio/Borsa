import ccxt
b = ccxt.binance({'options': {'defaultType': 'future'}})
b.load_markets()
try:
    # Notice we pass the spot symbol "MET/USDT"
    res = b.fetch_ohlcv("MET/USDT", "1h")
    print(f"Success! {len(res)}")
except Exception as e:
    with open("err.txt", "w") as f:
        f.write(type(e).__name__ + "\n" + str(e))
