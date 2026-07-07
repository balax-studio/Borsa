import asyncio
import ccxt.async_support as ccxt_async

async def main():
    # Force use of Binance futures exactly as the scanner does
    ex = ccxt_async.binance({'options': {'defaultType': 'future'}})
    try:
        res = await ex.fetch_ohlcv('SAND/USDT', '1d', limit=10)
        print("SAND/USDT success")
    except Exception as e:
        print(f"SAND/USDT exception: {type(e).__name__} {e}")
        
    try:
        res = await ex.fetch_ohlcv('SAND/USDT:USDT', '1d', limit=10)
        print("SAND/USDT:USDT success")
    except Exception as e:
        print(f"SAND/USDT:USDT exception: {type(e).__name__} {e}")

    try:
        res = await ex.fetch_ohlcv('SANDUSDT', '1d', limit=10)
        print("SANDUSDT success")
    except Exception as e:
        print(f"SANDUSDT exception: {type(e).__name__} {e}")

    await ex.close()

if __name__ == "__main__":
    asyncio.run(main())
