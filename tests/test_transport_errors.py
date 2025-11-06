import asyncio
import json
import pytest
import aiohttp
from typing import Any
from cuea.transport import Transport
from cuea.errors import AuthError, RateLimit, TransportError, NotFound, BadRequest

# small async no-op sleep to speed tests
async def _sleep_noop(*a, **k):
    return None

class DummyResp:
    def __init__(self, status: int, body: str = ""):
        self.status = status
        self._body = body
        # minimal attributes used when raising ClientResponseError
        self.request_info = None
        self.history = ()
        self.headers = {}

    async def text(self) -> str:
        return self._body

    async def json(self) -> Any:
        try:
            return json.loads(self._body)
        except Exception:
            raise

class DummyCtx:
    def __init__(self, resp: DummyResp, raise_on_enter: Exception = None):
        self._resp = resp
        self._raise = raise_on_enter

    async def __aenter__(self):
        if self._raise:
            raise self._raise
        return self._resp

    async def __aexit__(self, exc_type, exc, tb):
        return False

class DummySession:
    def __init__(self, resp: DummyResp = None, raise_on_request: Exception = None):
        self._resp = resp
        self._raise = raise_on_request

    def request(self, *a, **k):
        # return an async context manager
        if self._raise:
            # ensure raising inside "request" call to simulate connector errors
            raise self._raise
        return DummyCtx(self._resp)

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_401_maps_to_autherror(monkeypatch):
    # simulate repeated 401 responses then mapping to AuthError after retries
    t = Transport(base_url="http://x", max_retries=1, backoff_base=0.01, backoff_max=0.01)
    # set _session to dummy that always returns 401
    t._session = DummySession(DummyResp(401, '{"error":"unauthorized"}'))
    monkeypatch.setattr(asyncio, "sleep", _sleep_noop)
    with pytest.raises(AuthError):
        await t.request("GET", "/protected", recv_json=True)


@pytest.mark.asyncio
async def test_429_maps_to_ratelimit(monkeypatch):
    t = Transport(base_url="http://x", max_retries=1, backoff_base=0.01, backoff_max=0.01)
    t._session = DummySession(DummyResp(429, '{"error":"rate limit"}'))
    monkeypatch.setattr(asyncio, "sleep", _sleep_noop)
    with pytest.raises(RateLimit):
        await t.request("GET", "/rl", recv_json=True)


@pytest.mark.asyncio
async def test_404_maps_to_notfound(monkeypatch):
    t = Transport(base_url="http://x", max_retries=1, backoff_base=0.01, backoff_max=0.01)
    t._session = DummySession(DummyResp(404, 'not found'))
    monkeypatch.setattr(asyncio, "sleep", _sleep_noop)
    with pytest.raises(NotFound):
        await t.request("GET", "/missing", recv_json=False)


@pytest.mark.asyncio
async def test_400_maps_to_badrequest(monkeypatch):
    t = Transport(base_url="http://x", max_retries=1, backoff_base=0.01, backoff_max=0.01)
    t._session = DummySession(DummyResp(400, 'bad'))
    monkeypatch.setattr(asyncio, "sleep", _sleep_noop)
    with pytest.raises(BadRequest):
        await t.request("POST", "/bad", recv_json=False)


@pytest.mark.asyncio
async def test_500_maps_to_transporterror(monkeypatch):
    t = Transport(base_url="http://x", max_retries=1, backoff_base=0.01, backoff_max=0.01)
    t._session = DummySession(DummyResp(500, 'server error'))
    monkeypatch.setattr(asyncio, "sleep", _sleep_noop)
    with pytest.raises(TransportError):
        await t.request("GET", "/srv", recv_json=False)


@pytest.mark.asyncio
async def test_timeout_maps_to_transporterror(monkeypatch):
    # simulate session.request raising TimeoutError
    t = Transport(base_url="http://x", max_retries=1, backoff_base=0.01, backoff_max=0.01)
    t._session = DummySession(raise_on_request=asyncio.TimeoutError())
    monkeypatch.setattr(asyncio, "sleep", _sleep_noop)
    with pytest.raises(TransportError):
        await t.request("GET", "/tmo", recv_json=False)


@pytest.mark.asyncio
async def test_connector_error_maps_to_transporterror(monkeypatch):
    # simulate aiohttp.ClientConnectorError-like raise
    t = Transport(base_url="http://x", max_retries=1, backoff_base=0.01, backoff_max=0.01)
    # create a minimal ClientConnectorError. The constructor requires request_info and OSError.
    conn_err = aiohttp.ClientConnectorError(None, OSError("conn"))
    t._session = DummySession(raise_on_request=conn_err)
    monkeypatch.setattr(asyncio, "sleep", _sleep_noop)
    with pytest.raises(TransportError):
        await t.request("GET", "/conn", recv_json=False)
