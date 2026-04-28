import asyncio, sys, logging
# Suppress verbose CCXT debug noise
logging.basicConfig(level=logging.WARNING)
logging.getLogger('ccxt').setLevel(logging.WARNING)
sys.path.insert(0, '/app')

async def test():
    print(">>> Starting provider test", flush=True)
    try:
        from src.data.providers import get_provider_for_market
        print(">>> Import OK", flush=True)
    except Exception as e:
        print(f">>> IMPORT ERROR: {e}", flush=True)
        import traceback; traceback.print_exc()
        return

    class S:
        bybit_api_key = '2pBXPj9OK80aU0UjtU'
        bybit_secret = 'vWniVkJbXnQzSt2Ii7pU2uM7bxyf72tKUvjg'
        bybit_testnet = True

    try:
        p = get_provider_for_market('crypto', 'BTC/USDT', S())
        print(f">>> Provider created: {type(p)}", flush=True)
        print(f">>> Raw provider type: {type(p._provider)}", flush=True)
    except Exception as e:
        print(f">>> PROVIDER CREATE ERROR: {e}", flush=True)
        import traceback; traceback.print_exc()
        return

    try:
        print(">>> Calling fetch_ohlcv...", flush=True)
        candles = await p.fetch_ohlcv('1h', limit=3)
        print(f">>> Candles returned: {len(candles)}", flush=True)
        if candles:
            print(f">>> Last candle: {candles[-1]}", flush=True)
        else:
            print(">>> EMPTY - no candles returned", flush=True)
    except Exception as e:
        print(f">>> FETCH ERROR: {e}", flush=True)
        import traceback; traceback.print_exc()

    # Also test the normaliser import
    try:
        from src.data.normalizer import normalize_candle
        print(">>> normalize_candle import OK", flush=True)
    except Exception as e:
        print(f">>> normalize_candle IMPORT ERROR: {e}", flush=True)

asyncio.run(test())
print(">>> DONE", flush=True)
