import ccxt
b = ccxt.binance({'options': {'defaultType': 'future'}})
b.load_markets()

print("MET related symbols:")
for s in b.symbols:
    if "MET" in s:
        print(s)
