# src\cuea\adapters\bybit\adapter.py
import asyncio
from typing import Any, Optional
from .spot import SpotAdapter
from .futures import FuturesAdapter
from .options import OptionsAdapter
from .margin import MarginAdapter
from .._task_names import ADAPTER_TASK_NAMES


class ExchangeAdapter:
    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None, config: Optional[dict] = None) -> None:
        cfg = config or {}
        self.spot = SpotAdapter(api_key, secret, cfg)
        self.futures = FuturesAdapter(api_key, secret, cfg)
        self.options = OptionsAdapter(api_key, secret, cfg)
        self.margin = MarginAdapter(api_key, secret, cfg)

    def supports(self, market: str) -> bool:
        return getattr(self, market, None) is not None

    async def close(self) -> None:
        """
        Gracefully close transports and cancel background tasks for Bybit adapter.
        """
        async def _cancel_and_await(task: Any) -> None:
            if isinstance(task, asyncio.Task):
                try:
                    task.cancel()
                except Exception:
                    pass
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        for attr in ("spot", "futures", "options", "margin"):
            adapter = getattr(self, attr, None)
            if adapter is None:
                continue
            for task_name in ADAPTER_TASK_NAMES:
                task = getattr(adapter, task_name, None)
                if task:
                    await _cancel_and_await(task)
                    try:
                        setattr(adapter, task_name, None)
                    except Exception:
                        pass
            transport = getattr(adapter, "transport", None)
            if transport:
                try:
                    await transport.close()
                except Exception:
                    pass

    # Async context manager support
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
