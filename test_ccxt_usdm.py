import asyncio
import ccxt.async_support as ccxt

async def main():
    ex = ccxt.binanceusdm()
    try:
        await ex.load_markets()
        print("Success:", len(ex.symbols))
    except Exception as e:
        print("Error:", e)
    finally:
        await ex.close()

if __name__ == "__main__":
    asyncio.run(main())
