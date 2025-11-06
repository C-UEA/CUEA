import asyncio
from types import SimpleNamespace
from typing import Iterable

class MockWSMsg(SimpleNamespace):
    """
    Minimal stand-in for aiohttp WS message.
    Fields:
      - type: WSMsgType
      - data: str
    """
    pass

class MockWebSocket:
    """
    Async iterable that yields MockWSMsg objects provided in `messages`.
    """
    def __init__(self, messages: Iterable[dict], delay: float = 0.0):
        self._msgs = list(messages)
        self._delay = delay
        self.closed = False

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._msgs):
            # stop iteration
            raise StopAsyncIteration
        msg = self._msgs[self._i]
        self._i += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return MockWSMsg(type=msg["type"], data=msg["data"])

    async def send_json(self, payload):
        return None

    async def close(self):
        self.closed = True
