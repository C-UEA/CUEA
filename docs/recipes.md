# Recipes and Examples

This folder contains concise, copy-paste examples showing common usage patterns.

## 1) Auth setup and environment helper
Use `configs/.env.example` as template or export env vars directly:
```bash
export CUEA_EXCHANGE=binance
export CUEA_API_KEY=your_api_key
export CUEA_API_SECRET=your_api_secret
```

Use the config loader to build an adapter from env:
```py
from cuea.config import get_exchange_adapter_from_env
adapter = get_exchange_adapter_from_env(env_file='.env')
```

## 2) Quick sync-run helper (useful in scripts)
```py
from cuea import run, UnifiedClient

def main():
    async def _main():
        async with UnifiedClient('binance') as c:
            t = await c.fetch_ticker('BTC/USDT')
            print(t)
    run(_main)
```

## 3) Websocket user-data (listenKey) pattern (Binance spot)
```py
import asyncio
from cuea.adapters.binance.spot import SpotAdapter

async def user_stream_example(api_key, secret):
    adapter = SpotAdapter(api_key=api_key, secret=secret, config={})
    # monkeypatchable methods: _create_listen_key, _keepalive_listen_key in tests
    agen = adapter.ws_user_stream()
    try:
        async for ev in agen:
            # ev typically contains {"raw": payload} and sometimes "order" key
            print('event', ev)
    finally:
        await agen.aclose()
        await adapter.transport.close()

# run
# asyncio.run(user_stream_example('k','s'))
```

## 4) Graceful shutdown pattern
Use adapters or UnifiedClient as async context managers to ensure transports and background tasks are closed.
```py
from cuea import UnifiedClient
import asyncio

async def main():
    async with UnifiedClient('binance', api_key='k', secret='s') as c:
        # do work
        pass
asyncio.run(main())
```
