from cuea.models import Ticker, OrderRequest, Order, Position, FuturesOrder
from .transport import make_transport
from .common import to_decimal
from cuea.adapters._util_marketspec import MarketSpecWrapper
from decimal import Decimal
from typing import Optional, List, Dict, Any, AsyncIterator
import aiohttp
import asyncio
import json
import random

class FuturesAdapter:
    name = "binance_futures"
    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        base = cfg.get("base_url", "https://fapi.binance.com")
        self.transport = make_transport(api_key, secret, {"base_url": base, **cfg})
        self.spec = MarketSpecWrapper("binance", config or {})
        self._listen_key: Optional[str] = None
        self._keepalive_task: Optional[asyncio.Task] = None

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        params = {}
        if symbol:
            params["symbol"] = self.spec.normalize(symbol)
        data = await self.transport.request("GET", "/fapi/v1/openOrders", params=params, auth_required=True)
        orders = []
        if isinstance(data, list):
            for o in data:
                orders.append(Order(
                    id=str(o.get("orderId", "")),
                    symbol=o.get("symbol") or "",
                    side=(o.get("side") or "").lower(),
                    price=to_decimal(o.get("price") or 0),
                    qty=to_decimal(o.get("origQty") or 0),
                    filled=to_decimal(o.get("executedQty") or 0),
                    status=str(o.get("status") or "")
                ))
        return orders

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        params = {}
        if symbol:
            params["symbol"] = self.spec.normalize(symbol)
        if order_id.isdigit():
            params["orderId"] = order_id
        else:
            params["origClientOrderId"] = order_id
        try:
            await self.transport.request("DELETE", "/fapi/v1/order", params=params, auth_required=True)
            return True
        except Exception:
            return False

    async def fetch_ticker(self, symbol: str) -> Ticker:
        symbol = self.spec.normalize(symbol)

        # Prefer bookTicker (bid/ask). If missing or empty, fall back to ticker/price.
        data = None
        try:
            data = await self.transport.request("GET", "/fapi/v1/ticker/bookTicker", params={"symbol": symbol})
            if not data or (isinstance(data, dict) and not (data.get("bidPrice") or data.get("askPrice"))):
                data = None
        except Exception:
            data = None

        if data is None:
            try:
                data = await self.transport.request("GET", "/fapi/v1/ticker/price", params={"symbol": symbol})
            except Exception:
                data = {}

        bid = to_decimal(data.get("bidPrice", data.get("price")))
        ask = to_decimal(data.get("askPrice", data.get("price")))
        last = to_decimal(data.get("price"))
        return Ticker(symbol=symbol, bid=bid, ask=ask, last=last, timestamp=None)

    async def create_order(self, req: OrderRequest) -> FuturesOrder:
        payload = {
            "symbol": self.spec.normalize(req.symbol),
            "side": req.side.upper(),
            "type": req.type.upper(),
            "quantity": str(req.qty),
        }
        if req.price is not None:
            payload["price"] = str(req.price)
        payload.update(req.params or {})
        data = await self.transport.request("POST", "/fapi/v1/order", json=payload, auth_required=True)
        return FuturesOrder(
            id=str(data.get("orderId", "")),
            symbol=req.symbol,
            side=req.side,
            price=Decimal(str(data.get("price"))) if data.get("price") is not None else req.price,
            qty=Decimal(str(data.get("origQty") or req.qty)),
            filled=to_decimal(data.get("executedQty", 0)),
            status=str(data.get("status", "NEW")),
        )

    async def fetch_positions(self, symbol: Optional[str] = None) -> List[Position]:
        params = {}
        if symbol:
            params["symbol"] = self.spec.normalize(symbol)
        data = await self.transport.request("GET", "/fapi/v2/positionRisk", params=params, auth_required=True)
        positions = []
        if isinstance(data, list):
            for p in data:
                sym = p.get("symbol")
                pos_amt = to_decimal(p.get("positionAmt") or 0)
                entry = None
                if p.get("entryPrice"):
                    entry = to_decimal(p.get("entryPrice"))
                upnl = to_decimal(p.get("unRealizedProfit") or p.get("unrealizedProfit") or 0)
                lev = None
                try:
                    lev = Decimal(str(p.get("leverage"))) if p.get("leverage") is not None else None
                except Exception:
                    lev = None
                positions.append(Position(
                    symbol=sym,
                    size=pos_amt,
                    entry_price=entry,
                    unrealized_pnl=upnl,
                    leverage=lev,
                    margin=None,
                    liquid_price=to_decimal(p.get("liquidationPrice") or 0) if p.get("liquidationPrice") else None,
                    raw=p
                ))
        return positions

    # --- futures user-data WS implementation via /fapi/v1/listenKey ---
    async def _create_listen_key(self) -> str:
        headers = {}
        if getattr(self.transport, "api_key", None):
            headers["X-MBX-APIKEY"] = self.transport.api_key
        resp = await self.transport.request("POST", "/fapi/v1/listenKey", headers=headers, recv_json=True)
        if isinstance(resp, dict) and "listenKey" in resp:
            return resp["listenKey"]
        try:
            return str(resp)
        except Exception:
            raise RuntimeError("Failed to obtain Binance futures listenKey")

    async def _keepalive_listen_key(self, listen_key: str, interval: float = 60 * 30) -> None:
        headers = {}
        if getattr(self.transport, "api_key", None):
            headers["X-MBX-APIKEY"] = self.transport.api_key
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.transport.request("PUT", "/fapi/v1/listenKey", params={"listenKey": listen_key}, headers=headers, recv_json=True)
                except Exception:
                    return
        except asyncio.CancelledError:
            return

    async def ws_user_stream(self) -> AsyncIterator[Dict[str, Any]]:
        listen_key = await self._create_listen_key()
        self._listen_key = listen_key
        self._keepalive_task = asyncio.create_task(self._keepalive_listen_key(listen_key))
        url = f"wss://fstream.binance.com/ws/{listen_key}"
        backoff_base = 0.5
        backoff_cap = 60.0
        attempt = 0
        try:
            while True:
                attempt += 1
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.ws_connect(url, heartbeat=60) as ws:
                            attempt = 0
                            async for msg in ws:
                                try:
                                    if msg.type == aiohttp.WSMsgType.TEXT:
                                        try:
                                            payload = json.loads(msg.data)
                                        except json.JSONDecodeError as e:
                                            yield {"error": f"JSON decode error: {str(e)}"}
                                            continue
                                        out = {"raw": payload}
                                        evt_type = payload.get("e") or payload.get("eventType")
                                        if evt_type == "ORDER_TRADE_UPDATE" or payload.get("e") == "executionReport":
                                            o = payload.get("o") or payload
                                            order = Order(
                                                id=str(o.get("i") or o.get("orderId") or ""),
                                                symbol=o.get("s") or o.get("symbol") or "",
                                                side=(o.get("S") or o.get("side") or "").lower(),
                                                price=to_decimal(o.get("p") or o.get("price") or 0),
                                                qty=to_decimal(o.get("q") or o.get("origQty") or 0),
                                                filled=to_decimal(o.get("z") or o.get("executedQty") or 0),
                                                status=str(o.get("X") or o.get("status") or "")
                                            )
                                            out["order"] = order
                                        elif evt_type == "ACCOUNT_UPDATE":
                                            positions = []
                                            update = payload.get("a", {})
                                            for p in update.get("P", []):
                                                sym = p.get("s")
                                                size = to_decimal(p.get("pa", 0))
                                                ps = p.get("ps", "BOTH")
                                                if ps == "SHORT":
                                                    size = -size
                                                entry = to_decimal(p.get("ep"))
                                                upnl = to_decimal(p.get("up"))
                                                mt = p.get("mt")
                                                iw = to_decimal(p.get("iw")) if mt == "isolated" else None
                                                pos = Position(
                                                    symbol=sym,
                                                    size=size,
                                                    entry_price=entry,
                                                    unrealized_pnl=upnl,
                                                    leverage=None,
                                                    margin=iw,
                                                    liquid_price=None,
                                                    raw=p
                                                )
                                                positions.append(pos)
                                            if positions:
                                                out["positions"] = positions
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
                sleep_sec = min(backoff_cap, backoff_base * (2 ** (attempt - 1))) * (1 + random.random() * 0.3)
                await asyncio.sleep(sleep_sec)
                # continue to retry
        finally:
            if self._keepalive_task:
                try:
                    self._keepalive_task.cancel()
                    if isinstance(self._keepalive_task, asyncio.Task):
                        try:
                            await self._keepalive_task
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            pass
                except Exception:
                    pass
            if self._listen_key:
                headers = {}
                if getattr(self.transport, "api_key", None):
                    headers["X-MBX-APIKEY"] = self.transport.api_key
                try:
                    await self.transport.request("DELETE", "/fapi/v1/listenKey", params={"listenKey": self._listen_key}, headers=headers)
                except Exception:
                    pass
            self._listen_key = None
            self._keepalive_task = None
