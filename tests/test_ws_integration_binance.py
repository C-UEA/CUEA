import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock
from aiohttp import WSMsgType
from cuea.adapters.binance.spot import SpotAdapter
from cuea.adapters.binance.futures import FuturesAdapter
from tests._ws_mock import MockWebSocket

def _mk_text_msg(s: str):
    return {"type": WSMsgType.TEXT, "data": s}

def _load_fixture(name: str):
    p = Path(__file__).parent / "fixtures" / name
    return json.loads(p.read_text(encoding="utf-8"))

@pytest.mark.asyncio
async def test_binance_ws_user_stream_exec_report(monkeypatch):
    # load executionReport fixture and create a single TEXT msg
    payload = _load_fixture("binance_execution_report.json")
    msg_text = json.dumps(payload)
    ws = MockWebSocket([_mk_text_msg(msg_text)])
    # patch aiohttp.ClientSession to yield a context that returns ws
    class DummySessCtx:
        def __init__(self, ws):
            self._ws = ws
        async def __aenter__(self):
            return self._ws
        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummyClientSession:
        def __init__(self, *a, **k): pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        def ws_connect(self, url, heartbeat=None):
            return DummySessCtx(ws)

    monkeypatch.setattr("aiohttp.ClientSession", DummyClientSession)

    # instantiate adapter with dummy transport (no network calls used here for listenKey)
    adapter = SpotAdapter(api_key="k", secret="s", config={})
    # monkeypatch _create_listen_key to return a fake listenKey without calling REST
    monkeypatch.setattr(SpotAdapter, "_create_listen_key", AsyncMock(return_value="fake_key"))
    # monkeypatch _keepalive_listen_key to a no-op
    monkeypatch.setattr(SpotAdapter, "_keepalive_listen_key", AsyncMock(return_value=None))

    # collect first emitted event
    agen = adapter.ws_user_stream()
    ev = await agen.__anext__()  # get first item
    assert "raw" in ev
    assert ev["raw"]["e"] == "executionReport"
    assert "order" in ev
    order = ev["order"]
    assert order.id == str(payload["i"])
    await agen.aclose()

@pytest.mark.asyncio
async def test_binance_futures_ws_exec_report(monkeypatch):
    payload = _load_fixture("binance_futures_execution_report.json")
    msg_text = json.dumps(payload)
    ws = MockWebSocket([_mk_text_msg(msg_text)])
    class DummySessCtx:
        def __init__(self, ws):
            self._ws = ws
        async def __aenter__(self):
            return self._ws
        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummyClientSession:
        def __init__(self, *a, **k): pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        def ws_connect(self, url, heartbeat=None):
            return DummySessCtx(ws)

    monkeypatch.setattr("aiohttp.ClientSession", DummyClientSession)

    adapter = FuturesAdapter(api_key="k", secret="s", config={})
    monkeypatch.setattr(FuturesAdapter, "_create_listen_key", AsyncMock(return_value="fk"))
    monkeypatch.setattr(FuturesAdapter, "_keepalive_listen_key", AsyncMock(return_value=None))

    agen = adapter.ws_user_stream()
    ev = await agen.__anext__()
    assert "raw" in ev
    assert ev["raw"]["e"] == "ORDER_TRADE_UPDATE"
    assert "order" in ev
    order = ev["order"]
    assert order.id == str(payload["o"]["i"])
    await agen.aclose()
