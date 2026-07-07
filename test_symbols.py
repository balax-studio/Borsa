import ccxt
b = ccxt.binance({'options': {'defaultType': 'future'}})
b.load_markets()

symbols = ["MET/USDT", "MOVE/USDT", "SAND/USDT", "GIGGLE/USDT", "EDU/USDT", "BERA/USDT", "0G/USDT", "LAYER/USDT", "ROBO/USDT", "ANIME/USDT", "CFG/USDT", "EDEN/USDT", "PYTH/USDT", "VANA/USDT", "ALICE/USDT", "VIC/USDT", "DYM/USDT", "GALA/USDT", "IO/USDT", "JST/USDT"]

for s in symbols:
    # Need to check if the symbol exists in ccxt markets
    if s in b.markets or f"{s}:USDT" in b.markets:
        print(f"{s} is VALID.")
    else:
        print(f"{s} is INVALID.")
