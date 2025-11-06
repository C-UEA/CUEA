from cuea.models import Ticker, OrderRequest, Order, Balance, Trade
from .transport import make_transport
from .common import to_decimal
from cuea.adapters._util_marketspec import MarketSpecWrapper
from decimal import Decimal
from typing import Optional, AsyncIterator, List, Dict, Any
import aiohttp
import asyncio
import json
import random

class SpotAdapter:
    name = "binance_spot"

    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None, config: Optional[Dict[str, Any]] = None) -> None:
        self.transport = make_transport(api_key, secret, config or {})
        self.spec = MarketSpecWrapper("binance", config or {})
        # annotate instance attributes
        self._listen_key: Optional[str] = None
        self._listen_key_task: Optional[asyncio.Task] = None

    async def fetch_ticker(self, symbol: str) -> Ticker:
        symbol = self.spec.normalize(symbol)

        # Prefer bookTicker (bid/ask). If missing or empty, fall back to ticker/price.
        data = None
        try:
            data = await self.transport.request("GET", "/api/v3/ticker/bookTicker", params={"symbol": symbol})
            # treat empty dict/None as unusable
            if not data or (isinstance(data, dict) and not (data.get("bidPrice") or data.get("askPrice"))):
                data = None
        except Exception:
            data = None

        if data is None:
            # fallback to ticker/price which some mocks/tests return bid/ask on
            try:
                data = await self.transport.request("GET", "/api/v3/ticker/price", params={"symbol": symbol})
            except Exception:
                data = {}

        bid = to_decimal(data.get("bidPrice") or data.get("price"))
        ask = to_decimal(data.get("askPrice") or data.get("price"))
        last = to_decimal(data.get("price"))
        return Ticker(symbol=symbol, bid=bid, ask=ask, last=last, timestamp=None)

    async def create_order(self, req: OrderRequest) -> Order:
        payload = {
            "symbol": self.spec.normalize(req.symbol),
            "side": req.side.upper(),
            "type": req.type.upper(),
            "quantity": str(req.qty),
        }
        if req.price is not None:
            payload["price"] = str(req.price)
        data = await self.transport.request("POST", "/api/v3/order", json=payload, auth_required=True)
        return Order(
            id=str(data.get("orderId", "")),
            symbol=req.symbol,
            side=req.side,
            price=Decimal(str(data.get("price"))) if data.get("price") is not None else req.price,
            qty=Decimal(str(data.get("origQty") or req.qty)),
            filled=to_decimal(data.get("executedQty", 0)),
            status=str(data.get("status", "NEW")),
        )

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """
        Call /api/v3/openOrders (optionally with symbol).
        Returns a list of Order models for open orders.
        """
        params = {}
        if symbol:
            params["symbol"] = self.spec.normalize(symbol)
        data = await self.transport.request("GET", "/api/v3/openOrders", params=params, auth_required=True)
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
        """
        Cancel order on Binance spot using /api/v3/order (DELETE) with either orderId or origClientOrderId.
        """
        params = {}
        if symbol:
            params["symbol"] = self.spec.normalize(symbol)
        # prefer orderId param
        if order_id.isdigit():
            params["orderId"] = order_id
        else:
            params["origClientOrderId"] = order_id
        try:
            await self.transport.request("DELETE", "/api/v3/order", params=params, auth_required=True)
            return True
        except Exception:
            return False

    async def fetch_balances(self) -> List[Balance]:
        data = await self.transport.request("GET", "/api/v3/account", auth_required=True)
        balances = []
        for b in data.get("balances", []):
            balances.append(Balance(asset=b.get("asset"), free=to_decimal(b.get("free")), locked=to_decimal(b.get("locked"))))
        return balances

    # --- mapping helper for tests and WS usage ---

    @staticmethod
    def _map_trade_payload(payload: Dict[str, Any], symbol: str) -> Trade:
        trade_id = str(payload.get("t", ""))
        price = to_decimal(payload.get("p"))
        qty = to_decimal(payload.get("q"))
        is_buyer_maker = payload.get("m", False)
        side = "sell" if is_buyer_maker else "buy"
        ts = payload.get("T") or payload.get("E")
        return Trade(
            id=trade_id,
            symbol=symbol,
            price=Decimal(str(price)),
            qty=Decimal(str(qty)),
            side=side,
            timestamp=int(ts) if ts else None,
            raw=payload,
        )

    # --- Binance user-data WS (private) implementation ---

    async def _create_listen_key(self) -> str:
        headers = {}
        if getattr(self.transport, "api_key", None):
            headers["X-MBX-APIKEY"] = self.transport.api_key
        resp = await self.transport.request("POST", "/api/v3/userDataStream", headers=headers, recv_json=True)
        if isinstance(resp, dict) and "listenKey" in resp:
            return resp["listenKey"]
        if isinstance(resp, dict) and "listen_key" in resp:
            return resp["listen_key"]
        try:
            return str(resp)
        except Exception:
            raise RuntimeError("Failed to obtain Binance listenKey")

    async def _keepalive_listen_key(self, listen_key: str, interval: float = 60 * 30) -> None:
        headers = {}
        if getattr(self.transport, "api_key", None):
            headers["X-MBX-APIKEY"] = self.transport.api_key
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.transport.request("PUT", "/api/v3/userDataStream", params={"listenKey": listen_key}, headers=headers, recv_json=True)
                except Exception:
                    return
        except asyncio.CancelledError:
            return

    async def ws_user_stream(self) -> AsyncIterator[Dict[str, Any]]:
        listen_key = await self._create_listen_key()
        self._listen_key = listen_key
        self._listen_key_task = asyncio.create_task(self._keepalive_listen_key(listen_key))

        url = f"wss://stream.binance.com:9443/ws/{listen_key}"
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
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    try:
                                        payload = json.loads(msg.data)
                                    except Exception:
                                        continue
                                    evt_type = payload.get("e") or payload.get("eventType")
                                    out = {"raw": payload}
                                    if evt_type == "executionReport" or payload.get("e") == "executionReport":
                                        o = payload
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
                                    yield out
                                elif msg.type == aiohttp.WSMsgType.ERROR:
                                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    sleep_sec = min(backoff_cap, backoff_base * (2 ** (attempt - 1))) * (1 + random.random() * 0.3)
                    await asyncio.sleep(sleep_sec)
                    continue
        finally:
            # cancel keepalive task but only await real asyncio.Task instances
            if self._listen_key_task:
                try:
                    self._listen_key_task.cancel()
                    if isinstance(self._listen_key_task, asyncio.Task):
                        try:
                            await self._listen_key_task
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
                    await self.transport.request("DELETE", "/api/v3/userDataStream", params={"listenKey": self._listen_key}, headers=headers)
                except Exception:
                    pass
            self._listen_key = None
            self._listen_key_task = None
