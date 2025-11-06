import json
import hmac
import hashlib
from urllib.parse import urlencode
import pytest

# import the signer factory you updated
from cuea.adapters.bybit.auth import bybit_signer_factory

def _compact_json_str(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

@pytest.mark.parametrize("method", ["GET", "POST"])
def test_bybit_v5_signer_headers_match_local_compute(monkeypatch, method):
    # deterministic time
    fixed_ts = 1700000000000  # ms
    monkeypatch.setattr("time.time", lambda: fixed_ts / 1000.0)

    api_key = "testkey"
    secret = "testsecret"
    signer = bybit_signer_factory(api_key, secret)

    # request details
    path = "/v5/order/create"
    params = {"category": "option", "symbol": "BTC-USD"}
    body = {"qty": "1", "price": "100"} if method == "POST" else None

    # call signer. support both v5-style and legacy wrappers in auth implementation
    try:
        out = signer(method, path, params, body)
    except TypeError:
        # legacy style, try path/params
        out = signer(path, params)

    assert isinstance(out, dict)
    headers = out.get("headers") or {}
    assert headers.get("X-BAPI-API-KEY") == api_key
    assert "X-BAPI-TIMESTAMP" in headers
    assert "X-BAPI-SIGN" in headers
    # basic sanity: signature length and hex chars
    sig = headers.get("X-BAPI-SIGN")
    assert isinstance(sig, str)
    assert len(sig) >= 64
    int(sig, 16)  # should be hex-parsable

    # verify signature locally using the same algorithm the docs describe:
    ts = headers.get("X-BAPI-TIMESTAMP")
    recv = headers.get("X-BAPI-RECV-WINDOW", "5000")

    if method == "GET":
        # query string should be sorted
        qs = urlencode(sorted((k, "" if v is None else str(v)) for k, v in params.items()))
        expected_to_sign = str(ts) + api_key + str(recv) + qs
    else:
        body_str = _compact_json_str(body)
        expected_to_sign = str(ts) + api_key + str(recv) + body_str

    expected_sig = hmac.new(secret.encode(), expected_to_sign.encode(), hashlib.sha256).hexdigest()
    assert sig == expected_sig
