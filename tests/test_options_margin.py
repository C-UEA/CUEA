import pytest
from decimal import Decimal
from cuea import UnifiedClient
from cuea.models import OptionContract, OptionsChain, MarginBalance, MarginPosition, OptionsOrder

@pytest.mark.asyncio
async def test_models_validation():
    # OptionContract
    oc = OptionContract(
        symbol="BTC-30JUL25-30000-C",
        underlying="BTC/USDT",
        strike=Decimal("30000"),
        expiry=1690761600000,
        right="call"
    )
    assert oc.symbol.startswith("BTC")
    # OptionsChain container
    chain = OptionsChain(underlying="BTC/USDT", contracts=[oc])
    assert chain.underlying == "BTC/USDT"
    # Margin models
    mb = MarginBalance(asset="USDT", total=Decimal("100.0"), available=Decimal("90.0"))
    mp = MarginPosition(symbol="BTC/USDT", size=Decimal("0.01"), entry_price=Decimal("30000"), unrealized_pnl=Decimal("1.0"), margin_used=Decimal("10.0"))
    assert mb.asset == "USDT"
    assert mp.symbol == "BTC/USDT"

@pytest.mark.asyncio
async def test_unified_client_options_and_margin_binance(monkeypatch):
    async with UnifiedClient("binance") as uc:
        async def mock_request(method, path, **kwargs):
            if "options/assetInfo" in path or "options/contracts" in path:
                return {
                    "contracts": [
                        {
                            "symbol": "BTC-30JUL25-30000-C",
                            "underlying": "BTC/USDT",
                            "strike": "30000",
                            "expiry": 1732233600000,
                            "optionType": "CALL",
                            "optionStyle": "vanilla"
                        }
                    ]
                }
            elif "order" in path and method == "POST":
                return {"orderId": 9999, "price": "100", "origQty": "1", "filledQty": "0", "status": "NEW"}
            elif "/sapi/v1/margin/account" in path:
                return {
                    "userAssets": [
                        {"asset": "USDT", "free": "100", "locked": "0", "borrowed": "10"},
                    ]
                }
            elif "/sapi/v1/margin/isolated/account" in path:
                return {
                    "assets": [
                        {
                            "symbol": "BTCUSDT",
                            "positions": [
                                {
                                    "positionAmt": "0.01",
                                    "entryPrice": "30000",
                                    "unRealizedProfit": "1.0",
                                    "isolatedMargin": "10",
                                    "liquidationPrice": "20000"
                                }
                            ]
                        }
                    ]
                }
            return {}

        monkeypatch.setattr(uc.adapter.options.transport, "request", mock_request)
        monkeypatch.setattr(uc.adapter.margin.transport, "request", mock_request)

        chain = await uc.fetch_options_chain("BTC/USDT")
        assert isinstance(chain, OptionsChain)
        assert len(chain.contracts) == 1
        assert chain.contracts[0].strike == Decimal("30000")
        assert chain.contracts[0].right == "call"

        o = await uc.place_options_order("BTC-30JUL25-30000-C", "buy", qty=Decimal("1"), price=Decimal("100"))
        assert isinstance(o, OptionsOrder)
        assert o.id == "9999"

        balances = await uc.fetch_margin_balances()
        assert isinstance(balances, list)
        assert len(balances) == 1
        assert balances[0].asset == "USDT"
        assert balances[0].total == Decimal("110")
        assert balances[0].borrowed == Decimal("10")

        positions = await uc.fetch_margin_positions()
        assert isinstance(positions, list)
        assert len(positions) == 1
        p = positions[0]
        assert p.size == Decimal("0.01")
        assert p.unrealized_pnl == Decimal("1.0")

@pytest.mark.asyncio
async def test_unified_client_bybit_options_and_margin(monkeypatch):
    async with UnifiedClient("bybit") as uc:
        async def mock_request(method, path, **kwargs):
            if "/v5/market/instruments-info" in path:
                return {"result": {"list": [{"symbol": "BTC-231229-40000-C", "deliveryTime": "1703817600000"}]}}
            elif "/v5/order/create" in path:
                return {"result": {"orderId": "123", "qty": "1", "price": "100", "orderStatus": "NEW"}}
            elif "/v5/account/wallet-balance" in path:
                return {"result": {"list": [{"coin": [{"coin": "USDT", "walletBalance": "100", "availableToWithdraw": "90", "borrowAmount": "10"}]}]}}
            return {}

        monkeypatch.setattr(uc.adapter.options.transport, "request", mock_request)
        monkeypatch.setattr(uc.adapter.margin.transport, "request", mock_request)

        chain = await uc.fetch_options_chain("BTC/USDT")
        assert isinstance(chain, OptionsChain)
        assert len(chain.contracts) == 1
        assert chain.contracts[0].strike == Decimal("40000")
        assert chain.contracts[0].right == "call"

        o = await uc.place_options_order("BTC-231229-40000-C", "buy", qty=Decimal("1"), price=Decimal("100"))
        assert isinstance(o, OptionsOrder)
        assert o.id == "123"

        balances = await uc.fetch_margin_balances()
        assert isinstance(balances, list)
        assert len(balances) == 1
        assert balances[0].asset == "USDT"
        assert balances[0].total == Decimal("100")
        assert balances[0].borrowed == Decimal("10")

        positions = await uc.fetch_margin_positions()
        assert isinstance(positions, list)
        assert len(positions) == 0