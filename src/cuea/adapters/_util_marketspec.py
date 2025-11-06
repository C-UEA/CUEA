from typing import Any, Optional
_marketspec: Optional[Any]
try:
    import marketspec as _ms 
    _marketspec = _ms
except Exception:
    _marketspec = None

class MarketSpecWrapper:
    """
    Thin wrapper around QuantFolks/marketspec VenueSymbolResolver.

    If marketspec is not available at runtime this becomes a no-op pass-through.
    """
    def __init__(self, venue: str, config: Optional[dict] = None) -> None:
        self.venue = venue
        self.config = config or {}

    def normalize(self, spec_or_symbol: str) -> str:
        """
        Return venue symbol for a unified instrument spec or pass-through if unknown.
        
        Uses getattr to avoid static attribute errors if marketspec API changes.
        """
        if _marketspec is None:
            return spec_or_symbol
        resolver_cls = getattr(_marketspec, "VenueSymbolResolver", None)
        if resolver_cls is None:
            return spec_or_symbol
        try:
            resolver = resolver_cls(self.venue)
            # prefer `resolve` if present
            resolve_fn = getattr(resolver, "resolve", None)
            if callable(resolve_fn):
                return resolve_fn(spec_or_symbol)
            # fallback to str conversion
            return str(spec_or_symbol)
        except Exception:
            return spec_or_symbol
