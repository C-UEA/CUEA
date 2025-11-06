# src/cuea/transport.py

import aiohttp
from typing import Optional, Any, Callable, Dict
import asyncio
import random
from .rate_limiter import TokenBucket
from .errors import AuthError, RateLimit, TransportError, NotFound, BadRequest

# Signer is flexible: some adapters provide signer(path, params) others signer(path, method, params, json)
Signer = Callable[..., Dict[str, Any]]

def _safe_msg_from_exception(exc: BaseException) -> str:
    status = getattr(exc, "status", None)
    message = getattr(exc, "message", None)
    if message is None:
        if getattr(exc, "args", None):
            message = exc.args[0] if exc.args else None
    parts = []
    if status is not None:
        parts.append(f"status={status}")
    if message is not None:
        parts.append(f"message={repr(message)}")
    if not parts:
        return repr(exc)
    return ", ".join(parts)


class Transport:

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        secret: Optional[str] = None,
        timeout: int = 30,
        signer: Optional[Signer] = None,
        default_headers: Optional[Dict[str, str]] = None,
        rate_limiter: Optional[TokenBucket] = None,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.secret = secret
        self._session: Optional[aiohttp.ClientSession] = None
        self.timeout = timeout
        self._signer = signer
        self._default_headers = default_headers or {}
        self._rate_limiter = rate_limiter
        self.max_retries = int(max_retries)
        self.backoff_base = float(backoff_base)
        self.backoff_max = float(backoff_max)

    async def _ensure(self) -> None:
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)

    async def _maybe_acquire_rate(self, tokens: float = 1.0, use_rate_limit: bool = True) -> None:
        if use_rate_limit and self._rate_limiter is not None:
            await self._rate_limiter.acquire(tokens)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        auth_required: bool = False,
        recv_json: bool = True,
        timeout: Optional[int] = None,
        use_rate_limit: bool = True,
        retry_on: Optional[tuple] = None,
    ) -> Any:
        """
        Perform an HTTP request with retries and optional rate-limiting.
        """
        await self._ensure()
        session = self._session
        assert session is not None, "session must be created"

        params = dict(params or {})
        hdrs = dict(self._default_headers)
        if headers:
            hdrs.update(headers)

        # support flexible signer signatures:
        # Prefer v5-style signer(method, path, params, json_body) -> {"params":..., "headers":...}
        # Fallback to legacy signer(path, params) if the signer doesn't accept v5 signature.
        if auth_required and self._signer is not None:
            try:
                signed = None
                try:
                    # preferred v5 call
                    signed = self._signer(method.upper(), path, params if params else {}, json)
                except TypeError:
                    # fallback to legacy/simple call
                    signed = self._signer(path, params if params else {})
                if isinstance(signed, dict):
                    params = dict(signed.get("params", params))
                    hdrs.update(signed.get("headers", {}))
            except Exception as e:
                raise TransportError(_safe_msg_from_exception(e)) from None

        url = f"{self.base_url}/{path.lstrip('/')}"
        timeout_obj: Optional[aiohttp.ClientTimeout] = None
        if timeout is not None:
            timeout_obj = aiohttp.ClientTimeout(total=timeout)

        if retry_on is None:
            retry_on = tuple([429])

        attempt = 0
        while True:
            attempt += 1
            await self._maybe_acquire_rate(use_rate_limit=use_rate_limit)

            try:
                async with session.request(
                    method.upper(),
                    url,
                    params=params if params else None,
                    json=json if json is not None else None,
                    data=data if data is not None else None,
                    headers=hdrs if hdrs else None,
                    timeout=timeout_obj,
                ) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        should_retry = False
                        if resp.status in retry_on:
                            should_retry = True
                        elif 500 <= resp.status < 600:
                            should_retry = True

                        if not should_retry or attempt > self.max_retries:
                            status = resp.status
                            safe = f"status={status}, message={repr(text)}"
                            if status == 429:
                                raise RateLimit(safe)
                            if status in (401, 403):
                                raise AuthError(safe)
                            if status == 404:
                                raise NotFound(safe)
                            if 400 <= status < 500:
                                raise BadRequest(safe)
                            raise TransportError(safe)

                    else:
                        if recv_json:
                            try:
                                return await resp.json()
                            except Exception:
                                return text
                        return text

            except (aiohttp.ClientConnectorError, aiohttp.ClientOSError, asyncio.TimeoutError) as e:
                if attempt > self.max_retries:
                    raise TransportError(_safe_msg_from_exception(e)) from None

            except Exception as e:
                if isinstance(e, (AuthError, RateLimit, NotFound, BadRequest, TransportError)):
                    raise
                raise TransportError(_safe_msg_from_exception(e)) from None

            backoff = min(self.backoff_max, self.backoff_base * (2 ** (attempt - 1)))
            jitter = random.uniform(0, backoff * 0.3)
            delay = backoff + jitter
            await asyncio.sleep(delay)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
