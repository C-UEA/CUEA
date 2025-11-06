from cuea.transport import Transport
from .auth import binance_signer_factory
from cuea.rate_limiter import TokenBucket

def make_transport(api_key=None, secret=None, config=None):
    cfg = config or {}
    base = cfg.get("base_url", "https://api.binance.com")
    # only create signer when credentials are provided
    signer = binance_signer_factory(api_key, secret) if api_key and secret else None
    # configure simple token-bucket via config.rate_limit: {"capacity": 10, "refill_rate": 10}
    rl_cfg = cfg.get("rate_limit", {})
    if rl_cfg:
        capacity = rl_cfg.get("capacity", 10)
        refill = rl_cfg.get("refill_rate", 10)
        rate_limiter = TokenBucket(capacity=float(capacity), refill_rate=float(refill))
    else:
        rate_limiter = None
    return Transport(
        base_url=base,
        api_key=api_key,
        secret=secret,
        signer=signer,
        default_headers={"Content-Type": "application/json"},
        rate_limiter=rate_limiter,
        max_retries=cfg.get("max_retries", 3),
        backoff_base=cfg.get("backoff_base", 0.5),
        backoff_max=cfg.get("backoff_max", 10.0),
    )
