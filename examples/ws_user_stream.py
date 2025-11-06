# examples/ws_user_stream_futures.py
import asyncio
import os
from cuea.adapters.binance.futures import FuturesAdapter
from cuea.errors import AuthError

API_KEY = os.getenv("CUEA_API_KEY")
API_SECRET = os.getenv("CUEA_API_SECRET")

async def run_stream():
    if not API_KEY or not API_SECRET:
        print("set CUEA_API_KEY and CUEA_API_SECRET in env")
        return

    adapter = FuturesAdapter(api_key=API_KEY, secret=API_SECRET, config={})
    agen = adapter.ws_user_stream()
    try:
        async for ev in agen:
            print("event:", ev)
    except AuthError as e:
        print("Auth failed:", e)
    except KeyboardInterrupt:
        print("stopping")
    finally:
        await agen.aclose()
        if getattr(adapter, "transport", None):
            await adapter.transport.close()

if __name__ == "__main__":
    asyncio.run(run_stream())
