from __future__ import annotations

import asyncio
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def run_sync_task(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from a synchronous worker entrypoint.

    Worker threads often do not have a current event loop, so ``asyncio.run``
    is safer than relying on ``get_event_loop().run_until_complete(...)``.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    coro.close()
    raise RuntimeError(
        "Synchronous worker task bridge cannot run inside an active event loop"
    )
