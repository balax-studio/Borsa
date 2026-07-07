import asyncio
import time
from data_sources import async_get_crypto_data, async_get_crypto_1h_data

async def main():
    syms = ["MET/USDT", "SAND/USDT", "ALICE/USDT", "EDU/USDT"]
    for s in syms:
        t0 = time.time()
        res1d, res4h = await async_get_crypto_data(s)
        res1h = await async_get_crypto_1h_data(s)
        print(f"{s}: 1d is {res1d is not None}, 4h is {res4h is not None}, 1h is {res1h is not None}. Took {time.time()-t0:.2f}s")

if __name__ == "__main__":
    asyncio.run(main())
