# cuea — Crypto Unified Exchange Adapters

Lightweight async adapters that present a small, unified interface over exchange REST/WS endpoints.
Designed for algorithmic trading tools and integration tests.

## Install
```bash
pip install cuea
```

## Quick start (async)
```py
import asyncio
from cuea.registry import get_exchange_adapter

async def main():
    # create adapter for binance (spot + futures available)
    adapter = get_exchange_adapter("binance", api_key="...", secret="...")
    ticker = await adapter.spot.fetch_ticker("BTC/USDT")
    print(ticker)
    await adapter.close()

asyncio.run(main())
```

## Unified client helper (recommended)
```py
import asyncio
from cuea import UnifiedClient

async def example():
    async with UnifiedClient("binance", api_key="k", secret="s") as c:
        t = await c.fetch_ticker("BTC/USDT")
        print(t)
        # place a limit order (spot)
        # await c.place_limit_order("BTC/USDT", "buy", qty="0.001", price="30000")
asyncio.run(example())
```

You can also import the class directly:
```py
from cuea import UnifiedClient
async with UnifiedClient("binance") as c:
    ...
```

## Features
- Async HTTP transport with retries, backoff and optional token-bucket rate limiter.
- Adapters for `binance` and `bybit` (spot and futures stubs exist).
- Canonical models: `Ticker`, `OrderRequest`, `Order`, `Balance`, `Trade`, `Position`.
- Private user-data websocket helpers with listen-key lifecycle managed.

## API highlights
- `get_exchange_adapter(name, api_key=None, secret=None, config=None) -> ExchangeAdapter`
- `list_adapters() -> list[str]`
- `UnifiedClient(exchange, api_key=None, secret=None, config=None)` — convenience wrapper
  - `fetch_ticker(symbol)`
  - `place_limit_order(market, side, qty, price, market_type="spot")`
  - `place_market_order(...)`
  - `fetch_open_orders(symbol=None, market_type="spot")`
  - `fetch_positions(symbol=None, market_type="futures")`
  - `subscribe_trades(symbols, market_type="spot") -> AsyncIterator[Trade]`
  - async context manager and `close()` support

See inline docstrings in `src/cuea` for more details.

## Configuration
- Environment helper: `cuea.config.get_exchange_adapter_from_env(...)`
- `.env` example: `configs/.env.example`
- Per-exchange config example: `configs/default.yaml`
  - override `base_url`, `rate_limit`, `max_retries`, etc.

## Tests & linters
Run locally:
```bash
pip install -e . pytest ruff mypy
pytest -q
ruff check .
mypy src
```

## Contributing
See `CONTRIBUTING.md` for the contribution workflow, design notes and testing expectations.

## Notes & roadmap
- Options adapters are stubs. Implement only when you need options mappings.
- `marketspec` integration is optional and applied at runtime if available.
- Models use `pydantic` for clear validation.

## License
MIT
