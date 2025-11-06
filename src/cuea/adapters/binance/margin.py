from typing import Optional, Dict, Any, List
from decimal import Decimal
import asyncio
from cuea.adapters._util_marketspec import MarketSpecWrapper
from cuea.models import MarginBalance, MarginPosition
from .common import to_decimal
try:
    from .transport import make_transport  # type: ignore
except Exception:
    make_transport = None  # type: ignore

class MarginAdapter:
    name = "binance_margin"
    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.spec = MarketSpecWrapper("binance", cfg)
        self._api_key = api_key
        self._secret = secret
        self.config = cfg
        # Build transport if transport factory is available.
        self.transport = None
        if make_transport is not None:
            base = cfg.get("base_url", "https://api.binance.com")
            # make_transport handles signer creation when api_key/secret present
            self.transport = make_transport(api_key, secret, {"base_url": base, **cfg})
        self._listen_key: Optional[str] = None
        self._keepalive_task: Optional[asyncio.Task] = None

    async def fetch_margin_balances(self) -> List[MarginBalance]:
        """
        Call /sapi/v1/margin/account and map to MarginBalance.

        Works even if API key/secret are not set as long as transport is configured
        (tests monkeypatch the transport.request).
        """
        if self.transport is None:
            raise RuntimeError("Transport not configured for margin adapter")
        data = await self.transport.request("GET", "/sapi/v1/margin/account", auth_required=True)
        balances: List[MarginBalance] = []
        uas = data.get("userAssets") if isinstance(data, dict) else None
        if isinstance(uas, list):
            for a in uas:
                asset = a.get("asset") or a.get("coin") or None
                if asset is None:
                    continue
                free = to_decimal(a.get("free", 0))
                locked = to_decimal(a.get("locked", 0))
                borrowed = None
                try:
                    borrowed = to_decimal(a.get("borrowed", 0)) if a.get("borrowed") is not None else None
                except Exception:
                    borrowed = None
                total = free + locked + (borrowed or Decimal(0))
                balances.append(MarginBalance(
                    asset=asset,
                    total=total,
                    available=free,
                    borrowed=borrowed,
                    raw=a
                ))
            return balances
        # fallback shapes
        arr = None
        if isinstance(data, dict) and data.get("balances"):
            arr = data.get("balances")
        if isinstance(arr, list):
            for a in arr:
                asset = a.get("asset")
                free = to_decimal(a.get("free", 0))
                locked = to_decimal(a.get("locked", 0))
                borrowed = to_decimal(a.get("borrowed", 0)) if a.get("borrowed") is not None else None
                total = free + locked + (borrowed or Decimal(0))
                balances.append(MarginBalance(asset=asset, total=total, available=free, borrowed=borrowed, raw=a))
        return balances

    async def fetch_margin_positions(self) -> List[MarginPosition]:
        """
        Attempt to map isolated/cross margin positions.

        Works without credentials if transport is configured and returning
        mocked payloads (tests monkeypatch transport.request).
        """
        if self.transport is None:
            raise RuntimeError("Transport not configured for margin adapter")
        positions: List[MarginPosition] = []
        # try isolated account first
        try:
            data = await self.transport.request("GET", "/sapi/v1/margin/isolated/account", auth_required=True)
        except Exception:
            data = None
        try:
            if isinstance(data, dict):
                assets = data.get("assets") or data.get("userAssets") or None
                if isinstance(assets, list) and assets:
                    for a in assets:
                        symbol = a.get("symbol") or a.get("pair") or None
                        pos_list = a.get("positions") or a.get("position") or []
                        if isinstance(pos_list, list):
                            for p in pos_list:
                                try:
                                    size = to_decimal(p.get("positionAmt") or p.get("position") or p.get("qty") or 0)
                                    entry = to_decimal(p.get("entryPrice") or p.get("avgEntryPrice") or 0) if p.get("entryPrice") or p.get("avgEntryPrice") else None
                                    upnl = to_decimal(p.get("unRealizedProfit") or p.get("unrealizedProfit") or p.get("unrealised_pnl") or 0)
                                    margin_used = to_decimal(p.get("margin") or p.get("isolatedMargin") or 0)
                                    liq = to_decimal(p.get("liquidationPrice") or p.get("liquidation_price") or 0) if p.get("liquidationPrice") or p.get("liquidation_price") else None
                                    positions.append(MarginPosition(
                                        symbol=symbol or (p.get("symbol") or ""),
                                        size=size,
                                        entry_price=entry,
                                        unrealized_pnl=upnl,
                                        margin_used=margin_used,
                                        liquidation_price=liq,
                                        raw=p
                                    ))
                                except Exception:
                                    continue
                    if positions:
                        return positions
        except Exception:
            pass
        # try other endpoints (best-effort)
        try:
            data2 = await self.transport.request("GET", "/sapi/v1/margin/position", auth_required=True)
        except Exception:
            data2 = None
        try:
            if isinstance(data2, dict) and data2.get("positions"):
                for p in data2.get("positions", []):
                    try:
                        symbol = p.get("symbol") or p.get("s")
                        size = to_decimal(p.get("positionAmt") or p.get("position") or 0)
                        entry = to_decimal(p.get("entryPrice") or None) if p.get("entryPrice") else None
                        upnl = to_decimal(p.get("unRealizedProfit") or p.get("unrealizedProfit") or 0)
                        margin_used = to_decimal(p.get("margin") or 0)
                        liq = to_decimal(p.get("liquidationPrice") or 0) if p.get("liquidationPrice") else None
                        positions.append(MarginPosition(
                            symbol=symbol,
                            size=size,
                            entry_price=entry,
                            unrealized_pnl=upnl,
                            margin_used=margin_used,
                            liquidation_price=liq,
                            raw=p
                        ))
                    except Exception:
                        continue
                if positions:
                    return positions
        except Exception:
            pass
        # final attempt: positionRisk style (list)
        try:
            data3 = await self.transport.request("GET", "/sapi/v1/positionRisk", auth_required=True)
        except Exception:
            data3 = None
        try:
            if isinstance(data3, list):
                for p in data3:
                    try:
                        symbol = p.get("symbol") or ""
                        size = to_decimal(p.get("positionAmt") or 0)
                        entry = to_decimal(p.get("entryPrice") or None) if p.get("entryPrice") else None
                        upnl = to_decimal(p.get("unRealizedProfit") or p.get("unrealizedProfit") or 0)
                        margin_used = to_decimal(p.get("margin") or 0)
                        liq = to_decimal(p.get("liquidationPrice") or 0) if p.get("liquidationPrice") else None
                        positions.append(MarginPosition(
                            symbol=symbol,
                            size=size,
                            entry_price=entry,
                            unrealized_pnl=upnl,
                            margin_used=margin_used,
                            liquidation_price=liq,
                            raw=p
                        ))
                    except Exception:
                        continue
                if positions:
                    return positions
        except Exception:
            pass
        return []
