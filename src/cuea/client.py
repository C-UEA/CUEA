# src/cuea/client.py

from __future__ import annotations

from typing import Optional, List, AsyncIterator
from decimal import Decimal
import asyncio

from .registry import get_exchange_adapter
from .models import (
    OrderRequest,
    Order,
    Ticker,
    Position,
    Trade,
    OptionsChain,
    OptionsOrder,
    MarginBalance,
    MarginPosition,
)
from .adapters._util_marketspec import MarketSpecWrapper


class UnifiedClient:
    """
    Convenience wrapper that exposes a small, unified async API over exchange adapters.

    The client normalizes symbol resolution via MarketSpecWrapper when available.
    """

    def __init__(self, exchange: str, api_key: Optional[str] = None, secret: Optional[str] = None, config: Optional[dict] = None) -> None:
        self.exchange = exchange
        self.adapter = get_exchange_adapter(exchange, api_key=api_key, secret=secret, config=config or {})
        self._spec = MarketSpecWrapper(exchange, config or {})

    # --- market data / orders ---

    async def fetch_ticker(self, symbol: str) -> Ticker:
        """
        Fetch ticker from adapter.spot by default.
        """
        return await self.adapter.spot.fetch_ticker(self._spec.normalize(symbol))

    async def place_limit_order(self, market: str, side: str, qty: Decimal | str, price: Decimal | str, market_type: str = "spot", params: Optional[dict] = None) -> Order:
        adapter_market = getattr(self.adapter, market_type, None)
        if adapter_market is None:
            raise ValueError(f"Adapter does not support market type: {market_type}")
        symbol = self._spec.normalize(market)
        req = OrderRequest(symbol=symbol, side=side, qty=Decimal(str(qty)), type="limit", price=Decimal(str(price)), params=params or {})
        return await adapter_market.create_order(req)

    async def place_market_order(self, market: str, side: str, qty: Decimal | str, market_type: str = "spot", params: Optional[dict] = None) -> Order:
        adapter_market = getattr(self.adapter, market_type, None)
        if adapter_market is None:
            raise ValueError(f"Adapter does not support market type: {market_type}")
        symbol = self._spec.normalize(market)
        req = OrderRequest(symbol=symbol, side=side, qty=Decimal(str(qty)), type="market", params=params or {})
        return await adapter_market.create_order(req)

    async def fetch_positions(self, symbol: Optional[str] = None, market_type: str = "futures") -> List[Position]:
        """
        Fetch positions from a futures/margin market.
        """
        adapter_market = getattr(self.adapter, market_type, None)
        if adapter_market is None:
            raise ValueError(f"Adapter does not support market type: {market_type}")
        fn = getattr(adapter_market, "fetch_positions", None)
        if fn is None:
            raise NotImplementedError("fetch_positions not implemented by adapter")
        return await fn(symbol) if symbol else await fn()

    # ---- order management ----

    async def fetch_open_orders(self, symbol: Optional[str] = None, market_type: str = "spot") -> List[Order]:
        """
        Fetch open orders; symbol optional.
        """
        adapter_market = getattr(self.adapter, market_type, None)
        if adapter_market is None:
            raise ValueError(f"Adapter does not support market type: {market_type}")
        fn = getattr(adapter_market, "fetch_open_orders", None)
        if fn is None:
            raise NotImplementedError("fetch_open_orders not implemented by adapter")
        return await fn(symbol) if symbol else await fn()

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None, market_type: str = "spot") -> bool:
        """
        Cancel order by id. If adapter supports symbol-less cancel it may ignore symbol.
        Returns True if cancelled (or acknowledged), False otherwise.
        """
        adapter_market = getattr(self.adapter, market_type, None)
        if adapter_market is None:
            raise ValueError(f"Adapter does not support market type: {market_type}")
        fn = getattr(adapter_market, "cancel_order", None)
        if fn is None:
            raise NotImplementedError("cancel_order not implemented by adapter")
        # some adapters require symbol; pass along if available
        if symbol is None:
            return await fn(order_id)
        return await fn(order_id, symbol)

    # ---- trades subscription ----

    async def subscribe_trades(self, symbols: List[str], market_type: str = "spot") -> AsyncIterator[Trade]:
        """
        Subscribe to trade streams for multiple symbols concurrently.

        Yields Trade model instances from adapter_market.ws_subscribe_trades(symbol).
        Caller must `aclose()` the returned async generator or use cancellation.
        """
        adapter_market = getattr(self.adapter, market_type, None)
        if adapter_market is None:
            raise ValueError(f"Adapter does not support market type: {market_type}")

        queue: asyncio.Queue = asyncio.Queue()
        tasks: List[asyncio.Task] = []

        async def _producer(sym: str):
            try:
                async for tr in adapter_market.ws_subscribe_trades(self._spec.normalize(sym)):
                    await queue.put(tr)
            except asyncio.CancelledError:
                return
            except Exception:
                await queue.put(RuntimeError(f"producer failure for {sym}"))

        for s in symbols:
            tasks.append(asyncio.create_task(_producer(s)))

        try:
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---- options / margin helpers ----
    async def fetch_options_chain(self, underlying: str, market_type: str = "options") -> OptionsChain:
        """
        Fetch an options chain for an underlying from the adapter.
        """
        adapter_market = getattr(self.adapter, market_type, None)
        if adapter_market is None:
            raise ValueError(f"Adapter does not support market type: {market_type}")
        fn = getattr(adapter_market, "fetch_options_chain", None)
        if fn is None:
            raise NotImplementedError("fetch_options_chain not implemented by adapter")
        return await fn(underlying)

    async def place_options_order(self, market: str, side: str, qty: Decimal | str, price: Optional[Decimal | str] = None, market_type: str = "options", params: Optional[dict] = None) -> OptionsOrder:
        adapter_market = getattr(self.adapter, market_type, None)
        if adapter_market is None:
            raise ValueError(f"Adapter does not support market type: {market_type}")
        fn = getattr(adapter_market, "create_options_order", None)
        if fn is None:
            raise NotImplementedError("create_options_order not implemented by adapter")
        symbol = self._spec.normalize(market)
        req = OrderRequest(symbol=symbol, side=side, qty=Decimal(str(qty)), type="limit" if price is not None else "market", price=Decimal(str(price)) if price is not None else None, params=params or {})
        return await fn(req)

    async def fetch_margin_balances(self, market_type: str = "margin") -> List[MarginBalance]:
        """
        Fetch margin balances from adapter.
        """
        adapter_market = getattr(self.adapter, market_type, None)
        if adapter_market is None:
            raise ValueError(f"Adapter does not support market type: {market_type}")
        fn = getattr(adapter_market, "fetch_margin_balances", None)
        if fn is None:
            raise NotImplementedError("fetch_margin_balances not implemented by adapter")
        return await fn()

    async def fetch_margin_positions(self, market_type: str = "margin") -> List[MarginPosition]:
        """
        Fetch margin positions from adapter.
        """
        adapter_market = getattr(self.adapter, market_type, None)
        if adapter_market is None:
            raise ValueError(f"Adapter does not support market type: {market_type}")
        fn = getattr(adapter_market, "fetch_margin_positions", None)
        if fn is None:
            raise NotImplementedError("fetch_margin_positions not implemented by adapter")
        return await fn()

    # ---- lifecycle ----

    async def close(self) -> None:
        """
        Close underlying adapter (transports and background tasks) if adapter exposes .close()
        """
        close_coro = getattr(self.adapter, "close", None)
        if close_coro and callable(close_coro):
            c = close_coro()
            if asyncio.iscoroutine(c):
                await c

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
