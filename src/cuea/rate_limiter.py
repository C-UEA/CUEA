import asyncio
import time


class TokenBucket:
    """
    Simple async token-bucket.

    - capacity: max tokens in bucket.
    - refill_rate: tokens added per second.
    """

    def __init__(self, capacity: float, refill_rate: float) -> None:
        if capacity <= 0 or refill_rate <= 0:
            raise ValueError("capacity and refill_rate must be > 0")
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._cond = asyncio.Condition()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed <= 0:
            return
        add = elapsed * self.refill_rate
        if add > 0:
            self._tokens = min(self.capacity, self._tokens + add)
            self._last = now

    async def acquire(self, tokens: float = 1.0) -> None:
        """Acquire tokens. Waits until sufficient tokens are available."""
        if tokens <= 0:
            return
        async with self._cond:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # wait until next refill. Sleep small increment to remain responsive.
                # Use condition wait to be notified by put (not implemented). Simple sleep is acceptable.
                await asyncio.sleep(max(0.01, (tokens - self._tokens) / max(1e-6, self.refill_rate)))

    def try_acquire_now(self, tokens: float = 1.0) -> bool:
        """Non-blocking attempt to take tokens. Returns True if taken."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False
