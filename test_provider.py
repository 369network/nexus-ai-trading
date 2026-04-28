import asyncio, sys, logging
logging.basicConfig(level=logging.DEBUG)
sys.path.insert(0, '/app')

async def test():
    print(">>> Starting provider test", flush=True)
    try:
        from src.data.providers import get_provider_for_market
        print(">>> Import OK", flush=True)
    except Exception as e:
        print(f">>> IMPORT ERROR: {e}", flush=True)
        return

    class S:
        bybit_api_key = '2pBXPj9OK80aU0UjtU'
        bybit_secret = 'vWniVkJbXnQzSt2Ii7pU2uM7bxyf72tKUvjg'
        bybit_testnet = True

    try:
        p = get_provider_for_market('crypto', 'BTC/USDT', S())
        print(f">>> Provider created: {type(p)}", flush=True)
    except Exception as e:
        print(f">>> PROVIDER CREATE ERROR: {e}", flush=True)
        return

    try:
        candles = await p.fetch_ohlcv('1h', limit=3)
        print(f">>> Candles returned: {len(candles)}", flush=True)
        if candles:
            print(f">>> Last candle: {candles[-1]}", flush=True)
        else:
            print(">>> EMPTY - no candles returned", flush=True)
    except Exception as e:
        print(f">>> FETCH ERROR: {e}", flush=True)

asyncio.run(test())
print(">>> DONE", flush=True)
