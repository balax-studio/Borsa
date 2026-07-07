import asyncio
import ccxt.async_support as ccxt

async def main():
    ex = ccxt.kraken()
    try:
        await ex.load_markets()
        print("Kraken Success:", len(ex.symbols))
    except Exception as e:
        print("Error:", type(e).__name__, e)
    finally:
        await ex.close()

if __name__ == "__main__":
    asyncio.run(main())
