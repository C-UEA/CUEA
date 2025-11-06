import hmac
import hashlib
import time
from typing import Dict, Any, Optional
from urllib.parse import urlencode

def _now_ms() -> int:
    return int(time.time() * 1000)

def binance_signer_factory(api_key: Optional[str], secret: Optional[str]):
    """
    Return a signer callable for Binance REST API.

    Binance expects query-string HMAC SHA256 of the query for signed endpoints.
    The signer returns {"params": {...}, "headers": {...}}.
    """
    def signer(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if api_key is None or secret is None:
            raise ValueError("API key/secret required for signed request")
        # include timestamp if not present
        params = dict(params or {})
        if "timestamp" not in params:
            params["timestamp"] = _now_ms()
        if "recvWindow" not in params:
            params["recvWindow"] = 5000

        items = sorted((k, "" if v is None else str(v)) for k, v in params.items())
        query = urlencode(items)

        signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = signature
        headers = {"X-MBX-APIKEY": api_key}
        return {"params": params, "headers": headers}
    return signer
