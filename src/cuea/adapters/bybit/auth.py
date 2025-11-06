import hmac
import hashlib
import time
from typing import Dict, Any, Optional
from urllib.parse import urlencode
import json

def _now_ms() -> int:
    return int(time.time() * 1000)

def bybit_signer_factory(api_key: Optional[str], secret: Optional[str], recv_window: int = 5000):
    """
    Bybit v5 header-style signer.

    Returns a callable with signature:
        signer(method: str, path: str, params: Optional[Dict], body: Optional[Any]) -> {"params":..., "headers":...}

    This function does NOT implement legacy query-param signing.
    """
    if api_key is None or secret is None:
        def _raise_no_creds(*a, **k):
            raise ValueError("API key/secret required for signed request")
        return _raise_no_creds

    def signer(method: str, path: str, params: Optional[Dict[str, Any]] = None, body: Optional[Any] = None):
        ts = str(_now_ms())
        recv = str(recv_window)

        if method.upper() == "GET":
            qs = urlencode(sorted((k, "" if v is None else str(v)) for k, v in (params or {}).items()))
            payload = qs
        else:
            payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body else ""

        to_sign = ts + api_key + recv + payload
        signature = hmac.new(secret.encode(), to_sign.encode(), hashlib.sha256).hexdigest()

        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": recv,
            "Content-Type": "application/json",
        }
        # return both params and headers to allow transport to merge them as needed
        return {"params": params or {}, "headers": headers}

    return signer
