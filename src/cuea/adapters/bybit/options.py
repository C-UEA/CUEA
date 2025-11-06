from cuea.models import OptionsChain, OptionContract, OptionsOrder, OrderRequest, Order, Position
from .transport import make_transport
from .common import to_decimal
from cuea.adapters._util_marketspec import MarketSpecWrapper
from decimal import Decimal
from typing import Optional, Dict, Any, AsyncIterator, List
import aiohttp
import asyncio
import json
import time
import random
import hmac
import hashlib

class OptionsAdapter:
    name = "bybit_options"
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

    async def fetch_options_chain(self, underlying: str) -> OptionsChain:
        base_coin = underlying.upper().split('/')[0]  # e.g., 'BTC' from 'BTC/USDT'
        params = {"category": "option", "baseCoin": base_coin}
        data = await self.transport.request("GET", "/v5/market/instruments-info", params=params, auth_required=False)
        contracts = []
        for instr in data["result"]["list"]:
            symbol = instr["symbol"]
            parts = symbol.split('-')
            strike = Decimal('0')
            right = 'call'
            if len(parts) >= 4:
                strike = to_decimal(parts[2])
                right = 'call' if parts[3] == 'C' else 'put'
            expiry = int(instr["deliveryTime"]) if "deliveryTime" in instr else None
            contracts.append(
                OptionContract(
                    symbol=symbol,
                    underlying=underlying,
                    strike=strike,
                    expiry=expiry,
                    right=right,
                    raw=instr,
                )
            )
        return OptionsChain(underlying=underlying, contracts=contracts, raw=data)

    async def create_options_order(self, req: OrderRequest) -> OptionsOrder:
        payload = {
            "category": "option",
            "symbol": self.spec.normalize(req.symbol),
            "side": req.side.upper(),
            "orderType": req.type.upper(),
            "qty": str(req.qty),
        }
        if req.price is not None:
            payload["price"] = str(req.price)
        payload.update(req.params or {})
        data = await self.transport.request("POST", "/v5/order/create", json=payload, auth_required=True)
        result = data["result"]
        return OptionsOrder(
            id=result["orderId"],
            symbol=req.symbol,
            side=req.side,
            price=Decimal(result.get("price", req.price)) if req.price else None,
            qty=Decimal(result.get("qty", req.qty)),
            filled=to_decimal("0"),
            status="NEW",
        )

    def _bybit_ws_auth_payload(self) -> Dict[str, Any]:
        if not self._api_key or not self._secret:
            raise RuntimeError("API key/secret required for Bybit private WS auth")
        expires = int((time.time() + 2) * 1000)
        to_sign = f"GET/realtime{expires}"
        signature = hmac.new(bytes(self._secret, "utf-8"), bytes(to_sign, "utf-8"), digestmod=hashlib.sha256).hexdigest()
        return {"op": "auth", "args": [self._api_key, expires, signature]}

    async def ws_private(self, topics: List[str]) -> AsyncIterator[Dict[str, Any]]:
        url = self._private_ws_url
        backoff_base = 0.5
        backoff_cap = 60.0
        attempt = 0
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
                        # subscribe to requested private topics
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
                                    if topic.startswith("order"):
                                        orders = []
                                        for d in payload.get("data", []) if isinstance(payload.get("data"), list) else [payload.get("data")]:
                                            order = Order(
                                                id=str(d.get("orderId") or d.get("id") or ""),
                                                symbol=d.get("symbol") or "",
                                                side=(d.get("side") or "").lower(),
                                                price=to_decimal(d.get("price") or d.get("orderPrice") or 0),
                                                qty=to_decimal(d.get("qty") or d.get("size") or 0),
                                                filled=to_decimal(d.get("cumExecQty") or d.get("filledQty") or 0),
                                                status=str(d.get("orderStatus") or d.get("state") or "")
                                            )
                                            orders.append(order)
                                        if orders:
                                            out["orders"] = orders
                                    elif topic.startswith("position"):
                                        positions = []
                                        for d in payload.get("data", []) if isinstance(payload.get("data"), list) else [payload.get("data")]:
                                            side = d.get("side", "")
                                            size = to_decimal(d.get("size", 0))
                                            if side == "Sell":
                                                size = -size
                                            entry = to_decimal(d.get("entryPrice"))
                                            upnl = to_decimal(d.get("unrealisedPnl"))
                                            lev = to_decimal(d.get("leverage")) if d.get("leverage") else None
                                            liq = to_decimal(d.get("liqPrice")) if d.get("liqPrice") else None
                                            pos = Position(
                                                symbol=d.get("symbol"),
                                                size=size,
                                                entry_price=entry,
                                                unrealized_pnl=upnl,
                                                leverage=lev,
                                                margin=to_decimal(d.get("positionBalance")),
                                                liquid_price=liq,
                                                raw=d
                                            )
                                            positions.append(pos)
                                        if positions:
                                            out["positions"] = positions
                                    else:
                                        # yield raw private events
                                        yield {"raw": payload}
                                    if "orders" in out or "positions" in out:
                                        yield out
                                elif msg.type == aiohttp.WSMsgType.ERROR:
                                    yield {"error": str(ws.exception())}
                                    break
                                elif msg.type == aiohttp.WSMsgType.CLOSED:
                                    break
                            except Exception as e:
                                yield {"error": str(e)}
                                break
                        return
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
