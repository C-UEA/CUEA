
from cuea.registry import list_adapters, get_exchange_adapter
def test_list():
    adapters = list_adapters()
    assert "binance" in adapters
    assert "bybit" in adapters

def test_get_adapter():
    ex = get_exchange_adapter("binance")
    assert hasattr(ex, "spot")
