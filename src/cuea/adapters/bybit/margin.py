from typing import Optional, Dict, Any, List, AsyncIterator
import aiohttp
import asyncio
import json
import time
import random
import hmac
import hashlib
from cuea.adapters._util_marketspec import MarketSpecWrapper
from cuea.models import MarginBalance, MarginPosition
from .common import to_decimal
from .transport import make_transport

class MarginAdapter:
    name = "bybit_margin"
    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.spec = MarketSpecWrapper("bybit", cfg)
        self.api_key = api_key
        self.secret = secret
        self.config = cfg
        self.transport = make_transport(api_key, secret, cfg)
        self._api_key = api_key
        self._secret = secret
        self._private_ws_url = cfg.get("private_ws_url", "wss://stream.bybit.com/v5/private")

    async def fetch_margin_balances(self) -> List[MarginBalance]:
        data = await self.transport.request("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED"}, auth_required=True)
        balances = []
        for acct in data["result"]["list"]:
            for c in acct["coin"]:
                asset = c["coin"]
                total = to_decimal(c["walletBalance"])
                available = to_decimal(c.get("availableToWithdraw", c.get("free", "0")))
                borrowed = to_decimal(c.get("borrowAmount", "0"))
                if borrowed > 0 or total > 0:  # Include relevant balances
                    balances.append(
                        MarginBalance(
                            asset=asset,
                            total=total,
                            available=available,
                            borrowed=borrowed,
                            raw=c,
                        )
                    )
        return balances

    async def fetch_margin_positions(self) -> List[MarginPosition]:
        # Spot margin positions are not positional like futures; derive from balances or open margin orders if needed.
        # For simplicity, return empty list as spot margin is borrowing for spot trades.
        return []

    def _bybit_ws_auth_payload(self) -> Dict[str, Any]:
        if not self._api_key or not self._secret:
            raise RuntimeError("API key/secret required for Bybit private WS auth")
        expires = int((time.time() + 2) * 1000)
        to_sign = f"GET/realtime{expires}"
        signature = hmac.new(bytes(self._secret, "utf-8"), bytes(to_sign, "utf-8"), digestmod=hashlib.sha256).hexdigest()
        return {"op": "auth", "args": [self._api_key, expires, signature]}

    async def ws_user_stream(self) -> AsyncIterator[Dict[str, Any]]:
        url = self._private_ws_url
        backoff_base = 0.5
        backoff_cap = 60.0
        attempt = 0
        topics = ["wallet"]
        while True:
            attempt += 1
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url, heartbeat=30) as ws:
                        # authenticate
                        auth_payload = self._bybit_ws_auth_payload()
                        await ws.send_json(auth_payload)
                        # wait for auth response
                        auth_ok = False
                        async for msg in ws:
                            try:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    try:
                                        payload = json.loads(msg.data)
                                    except json.JSONDecodeError as e:
                                        yield {"error": f"JSON decode error: {str(e)}"}
                                        continue
                                    # look for auth success
                                    if payload.get("op") == "auth" and payload.get("success") is True:
                                        auth_ok = True
                                        break
                                    # if auth failed yield error
                                    if payload.get("op") == "auth" and payload.get("success") is False:
                                        yield {"error": f"Bybit WS auth failed: {payload}"}
                                        break
                                elif msg.type == aiohttp.WSMsgType.ERROR:
                                    yield {"error": str(ws.exception())}
                                    break
                                elif msg.type == aiohttp.WSMsgType.CLOSED:
                                    break
                            except Exception as e:
                                yield {"error": str(e)}
                                break
                        if not auth_ok:
                            yield {"error": "Bybit WS auth not confirmed"}
                            continue  # retry connection
                        # subscribe to wallet topic
                        sub_msg = {"op": "subscribe", "args": topics}
                        await ws.send_json(sub_msg)
                        # now stream messages and yield mapped events
                        async for msg in ws:
                            try:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    try:
                                        payload = json.loads(msg.data)
                                    except json.JSONDecodeError as e:
                                        yield {"error": f"JSON decode error: {str(e)}"}
                                        continue
                                    out = {"raw": payload}
                                    topic = payload.get("topic", "")
                                    if topic == "wallet":
                                        balances = []
                                        for acct in payload.get("data", []) if isinstance(payload.get("data"), list) else [payload.get("data")]:
                                            for c in acct.get("coin", []):
                                                asset = c.get("coin")
                                                total = to_decimal(c.get("walletBalance"))
                                                available = to_decimal(c.get("availableToWithdraw", c.get("free", "0")))
                                                borrowed = to_decimal(c.get("borrowAmount", "0"))
                                                if borrowed > 0 or total > 0:
                                                    bal = MarginBalance(
                                                        asset=asset,
                                                        total=total,
                                                        available=available,
                                                        borrowed=borrowed,
                                                        raw=c
                                                    )
                                                    balances.append(bal)
                                        if balances:
                                            out["margin_balances"] = balances
                                    yield out
                                elif msg.type == aiohttp.WSMsgType.ERROR:
                                    yield {"error": str(ws.exception())}
                                    break
                                elif msg.type == aiohttp.WSMsgType.CLOSED:
                                    break
                            except Exception as e:
                                yield {"error": str(e)}
                                break
            except aiohttp.ClientConnectionError as e:
                yield {"error": f"Connection error: {str(e)}"}
            except aiohttp.ClientError as e:
                yield {"error": f"Client error: {str(e)}"}
            except asyncio.TimeoutError as e:
                yield {"error": f"Timeout error: {str(e)}"}
            except Exception as e:
                yield {"error": f"Unexpected error: {str(e)}"}
            sleep_for = min(backoff_cap, backoff_base * (2 ** (attempt - 1))) * (1 + random.random() * 0.3)
            await asyncio.sleep(sleep_for)
            # continue to retry