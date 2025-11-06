# src/cuea/utils.py

import asyncio
import inspect
from typing import Any


def run(coro_or_fn: Any, *args, **kwargs):
    """
    Run an async function or coroutine from sync code.

    Usage:
      run(my_async_fn, arg1, arg2)
      run(my_coro)  # where my_coro is an awaitable

    Raises TypeError if argument is not coroutine or coroutine function.
    """
    if inspect.iscoroutine(coro_or_fn):
        return asyncio.run(coro_or_fn)
    if inspect.iscoroutinefunction(coro_or_fn):
        return asyncio.run(coro_or_fn(*args, **kwargs))
    raise TypeError("pass an async function or coroutine to run()")
