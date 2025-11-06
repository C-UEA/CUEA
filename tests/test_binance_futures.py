import pytest
from decimal import Decimal
from cuea.registry import get_exchange_adapter

@pytest.mark.asyncio
async def test_futures_fetch_ticker_create_order_fetch_positions(monkeypatch):
    adapter = get_exchange_adapter("binance")

    async def mock_request(method, path, **kwargs):
        p = path or ""
        if p.endswith("/ticker/price") or p.endswith("/ticker/price"):
            # futures ticker
            return {"symbol": "BTCUSDT", "bidPrice": "200.0", "askPrice": "201.0", "price": "200.5"}
        if p.endswith("/order") and method.upper() == "POST":
            return {"orderId": 5555, "price": "40000.0", "origQty": "0.01", "executedQty": "0.0", "status": "NEW"}
        if p.endswith("/openOrders"):
            return []
        if p.endswith("/positionRisk"):
            # return list of position objects as Binance futures does
            return [
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.5",
                    "entryPrice": "30000.0",
                    "unRealizedProfit": "10.0",
                    "leverage": "10",
                    "liquidationPrice": "25000.0"
                }
            ]
        if p.endswith("/listenKey"):
            return {"listenKey": "abc"}
        return {}

    # patch transport.request used by FuturesAdapter
    monkeypatch.setattr(adapter.futures, "transport", adapter.futures.transport)
    monkeypatch.setattr(adapter.futures.transport, "request", mock_request)

    # fetch ticker
    t = await adapter.futures.fetch_ticker("BTC/USDT")
    assert t.bid == Decimal("200.0")
    assert t.ask == Decimal("201.0")
    assert t.last == Decimal("200.5")

    # create order
    from cuea.models import OrderRequest
    req = OrderRequest(symbol="BTC/USDT", side="sell", qty=Decimal("0.01"), type="limit", price=Decimal("40000"))
    fo = await adapter.futures.create_order(req)
    assert fo.id == str(5555)
    assert fo.qty == Decimal("0.01")
    assert fo.price == Decimal("40000.0")

    # fetch positions
    positions = await adapter.futures.fetch_positions()
    assert isinstance(positions, list)
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "BTCUSDT"
    assert p.size == Decimal("0.5")
    assert p.entry_price == Decimal("30000.0")
    assert p.unrealized_pnl == Decimal("10.0")
    assert p.leverage == Decimal("10")
    assert p.liquid_price == Decimal("25000.0")
