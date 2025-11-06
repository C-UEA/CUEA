import json
from cuea.adapters.bybit.spot import SpotAdapter
from pathlib import Path
from decimal import Decimal

def test_bybit_trade_mapping():
    p = Path(__file__).parent / "fixtures" / "bybit_trade.json"
    payload = json.loads(p.read_text(encoding="utf-8"))
    trade = SpotAdapter._map_trade_payload(payload, "BTC/USDT")
    assert trade.id == str(payload["id"])
    assert trade.symbol == "BTC/USDT"
    assert trade.price == Decimal("29999.5")
    assert trade.qty == Decimal("0.002")
    assert trade.side == "buy"
    assert trade.timestamp == 1625256000456
    assert trade.raw["price"] == "29999.5"
