from typing import Optional, Dict, Any
from decimal import Decimal
from cuea.adapters._util_marketspec import MarketSpecWrapper
from cuea.models import OptionsChain, OptionContract, OptionsOrder, OrderRequest
from .transport import make_transport
from .common import to_decimal
import datetime
import re

class OptionsAdapter:
    name = "binance_options"

    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.spec = MarketSpecWrapper("binance", cfg)
        self.api_key = api_key
        self.secret = secret
        self.config = cfg
        base = cfg.get("base_url", "https://api.binance.com")
        self.transport = make_transport(api_key, secret, {"base_url": base, **cfg})

    async def _try_endpoints_for_chain(self, underlying: str) -> Optional[Any]:
        """
        Try multiple plausible endpoints that may return option chain data.
        Return the raw response or None.
        """
        candidates = [
            # public option endpoints often live under /eapi or /dapi depending on product.
            "/eapi/v1/public/options/assetInfo",   # hypothetical
            "/eapi/v1/public/options/contracts",   # hypothetical
            "/eapi/v1/public/optionInfo",          # hypothetical
            "/api/v1/options/contracts",           # fallback
            "/api/v3/options/contracts",           # fallback
            "/vapi/v1/public/options/contracts",   # by convention
        ]
        for p in candidates:
            try:
                resp = await self.transport.request("GET", p, params={"underlying": underlying}, auth_required=False)
                if resp:
                    return resp
            except Exception:
                continue
        return None

    def _parse_chain_from_payload(self, raw: Any, underlying: str) -> Optional[OptionsChain]:
        """
        Attempt to find contracts inside raw and map to OptionsChain.
        Heuristics for keys: 'contracts', 'data', 'result', 'symbols', 'rows'
        """
        if not raw:
            return None

        arr = None
        if isinstance(raw, dict):
            for k in ("contracts", "data", "result", "rows", "symbols"):
                v = raw.get(k)
                if isinstance(v, list) and v:
                    arr = v
                    break
            # some responses are nested under result->data
            if arr is None:
                maybe = raw.get("result") or raw.get("data")
                if isinstance(maybe, dict):
                    for k2 in ("contracts", "rows", "symbols", "options"):
                        v2 = maybe.get(k2)
                        if isinstance(v2, list) and v2:
                            arr = v2
                            break

        if arr is None and isinstance(raw, list):
            arr = raw

        if not arr:
            return None

        contracts = []
        for entry in arr:
            try:
                # common fields: symbol, strike, expiry, type/right, underlying
                symbol = entry.get("symbol") or entry.get("contract") or entry.get("id") or entry.get("name")
                if not symbol:
                    # try building a friendly symbol
                    base = entry.get("underlying") or underlying
                    s = entry.get("strike") or entry.get("strikePrice") or entry.get("k")
                    r = entry.get("optionType") or entry.get("side") or entry.get("type") or entry.get("right")
                    if s and r:
                        symbol = f"{base}-{s}-{r}"
                    else:
                        symbol = str(entry)
                strike = to_decimal(entry.get("strike") or entry.get("strikePrice") or entry.get("k") or 0)
                # expiry: could be ms timestamp, seconds, or ISO string
                expiry = None
                maybe = entry.get("expiry") or entry.get("expirationTime") or entry.get("expiryTime")
                if maybe is not None:
                    try:
                        if isinstance(maybe, (int, float)):
                            # assume milliseconds (preserve)
                            expiry = int(maybe)
                        else:
                            s_val = str(maybe).strip()
                            if re.fullmatch(r"\d+", s_val):
                                v = int(s_val)
                                # heuristic: >1e12 likely ms, else seconds -> convert to ms
                                expiry = v if v > 10**12 else v * 1000
                            else:
                                # try ISO parse (allow trailing Z)
                                try:
                                    dt = datetime.datetime.fromisoformat(s_val.replace("Z", "+00:00"))
                                    expiry = int(dt.timestamp() * 1000)
                                except Exception:
                                    expiry = None
                    except Exception:
                        expiry = None
                right_raw = (entry.get("optionType") or entry.get("right") or entry.get("type") or entry.get("side") or "").lower()
                right = "call" if "call" in right_raw or right_raw in ("c", "call") else ("put" if "put" in right_raw or right_raw in ("p", "put") else "call")
                contract = OptionContract(
                    symbol=symbol,
                    underlying=entry.get("underlying") or underlying,
                    strike=strike,
                    expiry=expiry,
                    right=right,
                    option_type=entry.get("optionStyle") or entry.get("option_type"),
                    raw=entry
                )
                contracts.append(contract)
            except Exception:
                # skip bad entry
                continue

        if not contracts:
            return None

        return OptionsChain(underlying=underlying, contracts=contracts, raw=raw)

    async def fetch_options_chain(self, underlying: str) -> OptionsChain:
        """
        Fetch option chain for an underlying.

        Raises NotImplementedError if a usable payload cannot be found.
        """
        # normalize underlying via marketspec wrapper (no-op if not installed)
        underlying_sym = self.spec.normalize(underlying)
        raw = await self._try_endpoints_for_chain(underlying_sym)
        parsed = self._parse_chain_from_payload(raw, underlying_sym)
        if parsed is None:
            raise NotImplementedError("Binance options: fetch_options_chain couldn't find a usable endpoint/shape")
        return parsed

    async def _try_endpoints_for_create_order(self, payload: Dict[str, Any]) -> Optional[Any]:
        """
        Try multiple plausible endpoints for creating an options order.
        """
        candidates = [
            "/eapi/v1/order",                # hypothetical
            "/eapi/v1/options/order",        # hypothetical
            "/api/v1/options/order",         # fallback
            "/api/v3/options/order",         # fallback
        ]
        for p in candidates:
            try:
                resp = await self.transport.request("POST", p, json=payload, auth_required=True)
                if resp:
                    return resp
            except Exception:
                continue
        return None

    async def create_options_order(self, req: OrderRequest) -> OptionsOrder:
        """
        Create an options order from an OrderRequest.

        Attempts several endpoints. Returns OptionsOrder on success or raises NotImplementedError.
        """
        payload: Dict[str, Any] = {
            "symbol": self.spec.normalize(req.symbol),
            "side": req.side.upper(),
            "type": req.type.upper(),
            "quantity": str(req.qty),
        }
        if req.price is not None:
            payload["price"] = str(req.price)
        payload.update(req.params or {})

        raw = await self._try_endpoints_for_create_order(payload)
        if not raw:
            raise NotImplementedError("Binance options: create_options_order couldn't find a usable endpoint/shape")

        # map common response keys
        order_id = None
        price = None
        qty = None
        status = "UNKNOWN"
        try:
            if isinstance(raw, dict):
                order_id = raw.get("orderId") or raw.get("id") or raw.get("order_id")
                price = raw.get("price") or raw.get("avgPrice") or payload.get("price")
                qty = raw.get("origQty") or raw.get("quantity") or payload.get("quantity")
                status = raw.get("status") or raw.get("state") or status
        except Exception:
            pass

        from cuea.models import OptionsOrder as _OptionsOrder
        return _OptionsOrder(
            id=str(order_id or ""),
            symbol=req.symbol,
            side=req.side,
            price=Decimal(str(price)) if price is not None else (req.price if req.price is not None else None),
            qty=Decimal(str(qty)) if qty is not None else Decimal(str(req.qty)),
            filled=to_decimal(raw.get("filledQty") if isinstance(raw, dict) else 0) if isinstance(raw, dict) else to_decimal(0),
            status=str(status),
        )
