import asyncio
import ccxt.async_support as ccxt_async

async def main():
    ex = ccxt_async.binance({'options': {'defaultType': 'future'}})
    try:
        await ex.fetch_ohlcv('WAL/USDT', '1d', limit=10)
        print("Success")
    except Exception as e:
        print(f"Exception Type: {type(e).__name__}")
        print(f"Exception: {e}")
    finally:
        await ex.close()

if __name__ == "__main__":
    asyncio.run(main())
