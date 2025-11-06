import json
from cuea.adapters.binance.spot import SpotAdapter
from pathlib import Path
from decimal import Decimal

def test_binance_trade_mapping():
    p = Path(__file__).parent / "fixtures" / "binance_trade.json"
    payload = json.loads(p.read_text(encoding="utf-8"))
    # call the mapping helper with a user symbol
    trade = SpotAdapter._map_trade_payload(payload, "BTC/USDT")
    assert trade.id == str(payload["t"])
    assert trade.symbol == "BTC/USDT"
    assert trade.price == Decimal("30000.12")
    assert trade.qty == Decimal("0.001")
    assert trade.side == "buy"  # m == false -> buy
    assert trade.timestamp == 1625256000123
    assert trade.raw["s"] == "BTCUSDT"
