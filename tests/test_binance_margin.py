import pytest
from decimal import Decimal
from cuea.registry import get_exchange_adapter
from cuea.models import MarginBalance, MarginPosition

@pytest.mark.asyncio
async def test_binance_margin_balances_and_positions(monkeypatch):
    adapter = get_exchange_adapter("binance", api_key="dummy_key", secret="dummy_secret")
    async def mock_request(method, path, **kwargs):
        p = path or ""
        # margin account balances
        if p.endswith("/sapi/v1/margin/account"):
            return {
                "userAssets": [
                    {"asset": "BTC", "free": "0.5", "locked": "0.0", "borrowed": "0.1"},
                    {"asset": "USDT", "free": "1000.0", "locked": "0.0", "borrowed": "0.0"},
                ]
            }
        # isolated account payload with nested positions
        if p.endswith("/sapi/v1/margin/isolated/account"):
            return {
                "assets": [
                    {
                        "symbol": "BTCUSDT",
                        "baseAsset": {},
                        "quoteAsset": {},
                        "positions": [
                            {
                                "symbol": "BTCUSDT",
                                "positionAmt": "0.2",
                                "entryPrice": "30000.0",
                                "unRealizedProfit": "5.0",
                                "isolatedMargin": "50.0",
                                "liquidationPrice": "25000.0"
                            }
                        ]
                    }
                ]
            }
        # fallback empty
        return {}
    monkeypatch.setattr(adapter.margin.transport, "request", mock_request)
    # balances
    balances = await adapter.margin.fetch_margin_balances()
    assert isinstance(balances, list)
    assert any(isinstance(b, MarginBalance) for b in balances)
    btc = next(b for b in balances if b.asset == "BTC")
    assert btc.total == Decimal("0.6")  # free + locked + borrowed
    assert btc.available == Decimal("0.5")
    assert btc.borrowed == Decimal("0.1")
    # positions
    positions = await adapter.margin.fetch_margin_positions()
    assert isinstance(positions, list)
    assert len(positions) == 1
    p = positions[0]
    assert isinstance(p, MarginPosition)
    assert p.symbol == "BTCUSDT"
    assert p.size == Decimal("0.2")
    assert p.entry_price == Decimal("30000.0")
    assert p.unrealized_pnl == Decimal("5.0")
    assert p.margin_used == Decimal("50.0")
    assert p.liquidation_price == Decimal("25000.0")