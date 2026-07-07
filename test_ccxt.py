import ccxt
import traceback

b = ccxt.binance({'options': {'defaultType': 'future'}})
try:
    b.fetch_ohlcv('MET/USDT', '1h')
except Exception as e:
    print("Exception class:", type(e).__name__)
