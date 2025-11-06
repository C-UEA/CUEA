from typing import Dict, Type, Any, List
from importlib import import_module
import os
import weakref
import asyncio

# registry maps exchange name -> ExchangeAdapter class
_registry: Dict[str, Type[Any]] = {}
# track live adapter instances (weakrefs) so callers can call close_all()
_instances: "weakref.WeakSet[Any]" = weakref.WeakSet()

def _discover_adapters() -> None:
    base = os.path.join(os.path.dirname(__file__), "adapters")
    if not os.path.isdir(base):
        return
    for name in os.listdir(base):
        if name.startswith("_") or not os.path.isdir(os.path.join(base, name)):
            continue
        try:
            mod = import_module(f"cuea.adapters.{name}.adapter")
            adapter_cls = getattr(mod, "ExchangeAdapter", None)
            if adapter_cls:
                _registry[name] = adapter_cls  # type: ignore[assignment]
        except Exception:
            # discovery should be best-effort; ignore broken adapters
            continue


_discover_adapters()


def get_exchange_adapter(name: str, **kwargs) -> Any:
    """
    Instantiate the registered exchange adapter and track it.

    Raises KeyError if adapter not found.
    """
    cls = _registry.get(name)
    if cls is None:
        raise KeyError(f"Exchange adapter not found: {name}")
    inst = cls(**kwargs)
    try:
        _instances.add(inst)
    except Exception:
        # If object not weakref-able skip tracking
        pass
    return inst


def list_adapters() -> List[str]:
    return list(_registry.keys())


async def close_all() -> None:
    """
    Close all tracked adapter instances by awaiting their `close()` coroutine if present.
    This function is safe to call multiple times and skips instances without a close method.
    """
    # snapshot to avoid modification during iteration
    objs = list(_instances)
    # await closes concurrently
    coros = []
    for obj in objs:
        close_coro = getattr(obj, "close", None)
        if callable(close_coro):
            try:
                # call and collect coroutine
                c = close_coro()
                if asyncio.iscoroutine(c):
                    coros.append(c)
            except Exception:
                # ignore synchronous exceptions; continue closing others
                continue
    if coros:
        await asyncio.gather(*coros, return_exceptions=True)
