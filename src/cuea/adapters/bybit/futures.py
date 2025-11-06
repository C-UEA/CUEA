from cuea.models import Ticker, OrderRequest, Order, Position, FuturesOrder
from .transport import make_transport
from .common import to_decimal
from cuea.adapters._util_marketspec import MarketSpecWrapper
from decimal import Decimal
from typing import Optional, List, Dict, Any, AsyncIterator
import aiohttp
import asyncio
import json
import time
import random
import hmac
import hashlib

class FuturesAdapter:
    name = "bybit_futures"
    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        self.transport = make_transport(api_key, secret, cfg)
        self.spec = MarketSpecWrapper("bybit", config)
        self.category = cfg.get("futures_category", "linear")  # 'linear' or 'inverse'
        self._api_key = api_key
        self._secret = secret
        self._private_ws_url = cfg.get("private_ws_url", "wss://stream.bybit.com/v5/private")

    async def fetch_ticker(self, symbol: str) -> Ticker:
        symbol = self.spec.normalize(symbol)
        params = {"category": self.category, "symbol": symbol}
        data = await self.transport.request("GET", "/v5/market/tickers", params=params)
        d = data["result"]["list"][0]
        bid = to_decimal(d["bid1Price"])
        ask = to_decimal(d["ask1Price"])
        last = to_decimal(d["lastPrice"])
        return Ticker(symbol=symbol, bid=bid, ask=ask, last=last, timestamp=None)

    async def create_order(self, req: OrderRequest) -> FuturesOrder:
        payload = {
            "category": self.category,
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
        return FuturesOrder(
            id=result["orderId"],
            symbol=req.symbol,
            side=req.side,
            price=Decimal(result.get("price", req.price)) if req.price else None,
            qty=Decimal(result.get("qty", req.qty)),
            filled=to_decimal("0"),  # Status via WS
            status="NEW",
        )

    async def fetch_positions(self, symbol: Optional[str] = None) -> List[Position]:
        params = {"category": self.category}
        if symbol:
            params["symbol"] = self.spec.normalize(symbol)
        data = await self.transport.request("GET", "/v5/position/list", params=params, auth_required=True)
        positions = []
        for p in data["result"]["list"]:
            size = to_decimal(p["size"])
            if p["side"] == "Sell":
                size = -size
            entry = to_decimal(p["avgPrice"])
            upnl = to_decimal(p["unrealisedPnl"])
            lev = to_decimal(p["leverage"])
            liq = to_decimal(p["liqPrice"]) if p["liqPrice"] else None
            positions.append(
                Position(
                    symbol=p["symbol"],
                    size=size,
                    entry_price=entry,
                    unrealized_pnl=upnl,
                    leverage=lev,
                    margin=None,
                    liquid_price=liq,
                    raw=p,
                )
            )
        return positions

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        params = {"category": self.category}
        if symbol:
            params["symbol"] = self.spec.normalize(symbol)
        data = await self.transport.request("GET", "/v5/order/realtime", params=params, auth_required=True)
        orders = []
        for o in data["result"]["list"]:
            orders.append(
                Order(
                    id=o["orderId"],
                    symbol=o["symbol"],
                    side=o["side"].lower(),
                    price=to_decimal(o["price"]),
                    qty=to_decimal(o["qty"]),
                    filled=to_decimal(o["cumExecQty"]),
                    status=o["orderStatus"],
                )
            )
        return orders

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        payload = {"category": self.category, "orderId": order_id}
        if symbol:
            payload["symbol"] = self.spec.normalize(symbol)
        try:
            await self.transport.request("POST", "/v5/order/cancel", json=payload, auth_required=True)
            return True
        except Exception:
            return False

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