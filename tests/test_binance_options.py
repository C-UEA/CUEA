import pytest
from decimal import Decimal
from cuea.registry import get_exchange_adapter
from cuea.models import OptionsChain, OptionContract, OptionsOrder, OrderRequest

@pytest.mark.asyncio
async def test_binance_options_chain_and_create_order(monkeypatch):
    adapter = get_exchange_adapter("binance")

    async def mock_request(method, path, **kwargs):
        p = path or ""
        # options chain candidate endpoint returns a dict with 'contracts'
        if method.upper() == "GET" and p.endswith("/eapi/v1/public/options/assetInfo"):
            return {
                "contracts": [
                    {
                        "symbol": "BTC-30JUL25-30000-C",
                        "underlying": "BTC/USDT",
                        "strike": "30000",
                        "expiry": 1732233600000,  # example ms timestamp
                        "optionType": "CALL",
                        "optionStyle": "vanilla"
                    },
                    {
                        "symbol": "BTC-30JUL25-30000-P",
                        "underlying": "BTC/USDT",
                        "strike": "30000",
                        "expiry": 1732233600000,
                        "optionType": "PUT",
                        "optionStyle": "vanilla"
                    }
                ]
            }
        # create order endpoint (POST) returns order-like dict
        if method.upper() == "POST" and (p.endswith("/eapi/v1/order") or p.endswith("/eapi/v1/options/order") or p.endswith("/api/v1/options/order")):
            body = kwargs.get("json") or {}
            return {
                "orderId": 9999,
                "price": body.get("price") or "1.0",
                "origQty": body.get("quantity") or body.get("quantity"),
                "filledQty": "0",
                "status": "NEW"
            }
        # fallback no-data
        return {}

    monkeypatch.setattr(adapter.options, "transport", adapter.options.transport)
    monkeypatch.setattr(adapter.options.transport, "request", mock_request)

    # fetch options chain
    chain = await adapter.options.fetch_options_chain("BTC/USDT")
    assert isinstance(chain, OptionsChain)
    assert chain.underlying in ("BTC/USDT", "BTC/USDT")
    assert len(chain.contracts) >= 2
    assert any(isinstance(c, OptionContract) for c in chain.contracts)

    # create options order
    req = OrderRequest(symbol="BTC-30JUL25-30000-C", side="buy", qty=Decimal("1"), type="limit", price=Decimal("1.5"))
    o = await adapter.options.create_options_order(req)
    assert isinstance(o, OptionsOrder)
    assert o.id == str(9999)
    assert o.qty == Decimal("1")
    assert o.price == Decimal("1.5") or o.price == Decimal("1.0") or o.price == Decimal("1.5")
    assert o.status.upper() in ("NEW", "NEW")
