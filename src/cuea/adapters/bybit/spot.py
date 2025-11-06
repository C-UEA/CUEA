from cuea.models import Ticker, OrderRequest, Order, Balance, Trade, Position
from .transport import make_transport
from .common import to_decimal
from cuea.adapters._util_marketspec import MarketSpecWrapper
from decimal import Decimal
from typing import Optional, AsyncIterator, List, Dict, Any
import aiohttp
import asyncio
import json
import time
import random
import hmac
import hashlib

class SpotAdapter:
    name = "bybit_spot"
    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None, config: Optional[Dict[str, Any]] = None) -> None:
        self.transport = make_transport(api_key, secret, config or {})
        self.spec = MarketSpecWrapper("bybit", config or {})
        self._api_key = api_key
        self._secret = secret
        self._private_ws_url: str = (config or {}).get("private_ws_url", "wss://stream.bybit.com/v5/private")

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        params = {"category": "spot"}
        if symbol:
            params["symbol"] = self.spec.normalize(symbol)
        data = await self.transport.request("GET", "/v5/order/realtime", params=params, auth_required=True)
        orders = []
        if isinstance(data, dict) and data.get("result"):
            for o in data.get("result", {}).get("list", []):
                orders.append(Order(
                    id=str(o.get("orderId") or o.get("order_id") or ""),
                    symbol=o.get("symbol") or "",
                    side=(o.get("side") or "").lower(),
                    price=to_decimal(o.get("price") or 0),
                    qty=to_decimal(o.get("qty") or o.get("quantity") or 0),
                    filled=to_decimal(o.get("cumExecQty") or 0),
                    status=str(o.get("orderStatus") or "")
                ))
        return orders

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        params = {"category": "spot", "orderId": order_id}
        if symbol:
            params["symbol"] = self.spec.normalize(symbol)
        try:
            await self.transport.request("POST", "/v5/order/cancel", json=params, auth_required=True)
            return True
        except Exception:
            return False

    async def fetch_ticker(self, symbol: str) -> Ticker:
        symbol = self.spec.normalize(symbol)
        params = {"category": "spot", "symbol": symbol}
        data = await self.transport.request("GET", "/v5/market/tickers", params=params)
        if isinstance(data, dict) and data.get("result"):
            d = data["result"]["list"][0]
            bid = to_decimal(d.get("bid1Price"))
            ask = to_decimal(d.get("ask1Price"))
            last = to_decimal(d.get("lastPrice"))
            return Ticker(symbol=symbol, bid=bid, ask=ask, last=last, timestamp=None)
        return Ticker(symbol=symbol, bid=Decimal("0"), ask=Decimal("0"), last=Decimal("0"), timestamp=None)

    async def create_order(self, req: OrderRequest) -> Order:
        params = {
            "category": "spot",
            "symbol": self.spec.normalize(req.symbol),
            "side": req.side.upper(),
            "orderType": req.type.upper(),
            "qty": str(req.qty),
        }
        if req.price is not None:
            params["price"] = str(req.price)
        data = await self.transport.request("POST", "/v5/order/create", json=params, auth_required=True)
        return Order(
            id=str(data.get("result", {}).get("orderId", "")),
            symbol=req.symbol,
            side=req.side,
            price=Decimal(str(data.get("result", {}).get("price"))) if data.get("result", {}).get("price") is not None else req.price,
            qty=Decimal(str(data.get("result", {}).get("qty") or req.qty)),
            filled=to_decimal(data.get("result", {}).get("cumExecQty", 0)),
            status=str(data.get("result", {}).get("orderStatus", "NEW")),
        )

    async def fetch_balances(self) -> List[Balance]:
        data = await self.transport.request("GET", "/v5/account/wallet-balance", params={"accountType": "SPOT"}, auth_required=True)
        balances = []
        if isinstance(data, dict):
            result = data.get("result") or {}
            for acct in result.get("list", []):
                for c in acct.get("coin", []):
                    free = to_decimal(c.get("availableToWithdraw", c.get("free", 0)))
                    locked = to_decimal(c.get("lockedBalance") or 0)
                    balances.append(Balance(asset=c.get("coin"), free=free, locked=locked))
        return balances

    # --- mapping helper for tests and WS usage ---
    @staticmethod
    def _map_trade_payload(payload: Dict[str, Any], symbol: str) -> Trade:
        """
        Map Bybit public trade payload/data element to cuea.models.Trade.
        Bybit public WS payloads differ; typical per-trade item may look like:
        {"id": 12345, "price": "65000", "size": "0.1", "side": "Buy", "ts": 1620000000000}
        """
        trade_id = str(payload.get("id", "") or payload.get("trade_id", ""))
        price = to_decimal(payload.get("price") or payload.get("p"))
        qty = to_decimal(payload.get("size") or payload.get("qty") or payload.get("q"))
        side_raw = payload.get("side") or payload.get("S") or payload.get("direction")
        side = None
        if isinstance(side_raw, str):
            side = side_raw.lower()
            if side in ("buy", "b", "buying", "long"):
                side = "buy"
            elif side in ("sell", "s", "selling", "short"):
                side = "sell"
        if side is None:
            side = "buy"
        ts = payload.get("ts") or payload.get("trade_time_ms") or payload.get("T")
        return Trade(
            id=trade_id,
            symbol=symbol,
            price=Decimal(str(price)),
            qty=Decimal(str(qty)),
            side=side,
            timestamp=int(ts) if ts else None,
            raw=payload,
        )

    # --- Bybit private websocket ---
    def _bybit_ws_auth_payload(self) -> Dict[str, Any]:
        """
        Build Bybit v5 WS auth payload.
        Signature per docs: hmac_sha256(secret, f"GET/realtime{expires}")
        expires is ms timestamp > now.
        """
        if not self._api_key or not self._secret:
            raise RuntimeError("API key/secret required for Bybit private WS auth")
        expires = int((time.time() + 2) * 1000)
        to_sign = f"GET/realtime{expires}"
        signature = hmac.new(bytes(self._secret, "utf-8"), bytes(to_sign, "utf-8"), digestmod=hashlib.sha256).hexdigest()
        return {"op": "auth", "args": [self._api_key, expires, signature]}

    async def ws_private(self, topics: List[str]) -> AsyncIterator[Dict[str, Any]]:
        """
        Connect to Bybit private WS, authenticate, subscribe to topics and yield events.
        topics example: ["order.BTCUSDT", "position.BTCUSDT"]
        """
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
                                    data = payload.get("data", [])
                                    if topic.startswith("order"):
                                        # map to Order if possible
                                        orders = []
                                        for d in data if isinstance(data, list) else [data]:
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
                                    elif topic.startswith("execution"):
                                        executions = []
                                        for d in data if isinstance(data, list) else [data]:
                                            exec_info = {
                                                "execId": d.get("execId"),
                                                "symbol": d.get("symbol"),
                                                "side": (d.get("side") or "").lower(),
                                                "price": to_decimal(d.get("execPrice")),
                                                "qty": to_decimal(d.get("execQty")),
                                                "fee": to_decimal(d.get("execFee")),
                                                "orderId": d.get("orderId"),
                                                "type": d.get("execType"),
                                                "timestamp": int(d.get("execTime")) if d.get("execTime") else None,
                                            }
                                            executions.append(exec_info)
                                        if executions:
                                            out["executions"] = executions
                                    elif topic.startswith("wallet"):
                                        balances = []
                                        for acct in data if isinstance(data, list) else [data]:
                                            for coin in acct.get("coin", []):
                                                asset = coin.get("coin")
                                                free = to_decimal(coin.get("availableToWithdraw", coin.get("free", 0)))
                                                total = to_decimal(coin.get("walletBalance"))
                                                locked = total - free
                                                bal = Balance(
                                                    asset=asset,
                                                    free=free,
                                                    locked=locked,
                                                )
                                                balances.append(bal)
                                        if balances:
                                            out["balances"] = balances
                                    elif topic.startswith("position"):
                                        positions = []
                                        for d in data if isinstance(data, list) else [data]:
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
                                    if out.get("orders") or out.get("executions") or out.get("balances") or out.get("positions"):
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

    async def ws_user_stream(self) -> AsyncIterator[Dict[str, Any]]:
        """
        Convenience method for spot user data stream, subscribing to spot-specific topics.
        """
        spot_topics = ["order.spot", "execution.spot", "wallet"]
        async for event in self.ws_private(spot_topics):
            yield event