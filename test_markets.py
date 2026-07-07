import asyncio
import ccxt.async_support as ccxt_async

async def main():
    ex = ccxt_async.binance({'options': {'defaultType': 'future'}})
    try:
        markets = await ex.load_markets()
        print(f"Total loaded markets: {len(markets)}")
        
        # Check types of markets
        spot_count = sum(1 for m in markets.values() if m.get('type') == 'spot')
        swap_count = sum(1 for m in markets.values() if m.get('type') == 'swap')
        future_count = sum(1 for m in markets.values() if m.get('type') == 'future')
        print(f"Spot: {spot_count}, Swap: {swap_count}, Future: {future_count}")
        
        # Check if WAL/USDT is in there
        print("WAL/USDT in markets?", 'WAL/USDT' in markets)
        if 'WAL/USDT' in markets:
            print("WAL/USDT type:", markets['WAL/USDT'].get('type'))

    finally:
        await ex.close()

if __name__ == "__main__":
    asyncio.run(main())
