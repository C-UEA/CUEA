import pytest
from unittest.mock import AsyncMock
from cuea.registry import get_exchange_adapter, close_all

@pytest.mark.asyncio
async def test_exchange_adapter_context_manager_closes_transport(monkeypatch):
    adapter = get_exchange_adapter("binance", api_key="k", secret="s", config={})
    adapter.spot.transport.close = AsyncMock()
    adapter.futures.transport.close = AsyncMock()

    # use async context manager
    async with adapter:
        # inside context nothing should be closed yet
        assert not adapter.spot.transport.close.called
        assert not adapter.futures.transport.close.called

    assert adapter.spot.transport.close.await_count >= 1
    assert adapter.futures.transport.close.await_count >= 1

@pytest.mark.asyncio
async def test_registry_close_all(monkeypatch):
    # create two adapters and ensure they are tracked
    a1 = get_exchange_adapter("binance", api_key="k", secret="s", config={})
    a2 = get_exchange_adapter("bybit", api_key="k", secret="s", config={})

    # patch their close methods
    a1.close = AsyncMock()
    a2.close = AsyncMock()

    # call close_all and assert both close coroutines were awaited
    await close_all()
    assert a1.close.await_count >= 1
    assert a2.close.await_count >= 1
