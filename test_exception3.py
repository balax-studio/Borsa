import ccxt.async_support as ccxt
import asyncio

async def test():
    b = ccxt.binance({'options': {'defaultType': 'future'}})
    await b.load_markets()
    try:
        res = await b.fetch_ohlcv("MET/USDT", "4h", limit=300)
        print(f"Success! {len(res)}")
    except Exception as e:
        with open("err.txt", "w") as f:
            f.write(type(e).__name__ + "\n" + str(e))
    await b.close()

if __name__ == "__main__":
    asyncio.run(test())
