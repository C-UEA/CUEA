from decimal import Decimal
import pytest
from cuea.registry import get_exchange_adapter
from cuea.models import Trade

@pytest.mark.asyncio
async def test_spot_fetch_ticker_create_order_fetch_balances_and_trade_map(monkeypatch):
    adapter = get_exchange_adapter("binance")

    async def mock_request(method, path, **kwargs):
        # simple router by path used in SpotAdapter
        p = path or ""
        if p.endswith("/ticker/price") or p.endswith("/ticker/price"):
            # ticker endpoint
            return {"symbol": "BTCUSDT", "bidPrice": "100.0", "askPrice": "101.0", "price": "100.5"}
        if p.endswith("/order") and method.upper() == "POST":
            # create order response
            return {"orderId": 12345, "price": "30000.0", "origQty": "0.001", "executedQty": "0.000", "status": "NEW"}
        if p.endswith("/openOrders"):
            return []
        if p.endswith("/account"):
            return {"balances": [{"asset": "BTC", "free": "1.0", "locked": "0.1"}, {"asset": "USDT", "free": "1000.0", "locked": "0.0"}]}
        # default
        return {}

    # patch transport.request used by SpotAdapter
    monkeypatch.setattr(adapter.spot, "transport", adapter.spot.transport)
    monkeypatch.setattr(adapter.spot.transport, "request", mock_request)

    # fetch ticker
    t = await adapter.spot.fetch_ticker("BTC/USDT")
    assert t.symbol in ("BTCUSDT", "BTC/USDT", "BTC/USDT".replace("/", ""))  # tolerant
    assert isinstance(t.bid, Decimal)
    assert t.bid == Decimal("100.0")
    assert t.ask == Decimal("101.0")
    assert t.last == Decimal("100.5")

    # create order
    from cuea.models import OrderRequest
    req = OrderRequest(symbol="BTC/USDT", side="buy", qty=Decimal("0.001"), type="limit", price=Decimal("30000.0"))
    order = await adapter.spot.create_order(req)
    assert order.id == str(12345)
    assert order.qty == Decimal("0.001") or order.qty == Decimal("0.001")
    assert order.status.upper() in ("NEW", "NEW")

    # fetch balances
    balances = await adapter.spot.fetch_balances()
    assert any(b.asset == "BTC" for b in balances)
    btc = next(b for b in balances if b.asset == "BTC")
    assert btc.free == Decimal("1.0")
    assert btc.locked == Decimal("0.1")

    # test trade mapping helper
    payload = {"t": 99, "p": "100.0", "q": "0.5", "m": False, "T": 1620000000000}
    trade = adapter.spot._map_trade_payload(payload, "BTC/USDT")
    assert isinstance(trade, Trade)
    assert trade.price == Decimal("100.0")
    assert trade.qty == Decimal("0.5")
    assert trade.side == "buy"
    assert trade.timestamp == 1620000000000
