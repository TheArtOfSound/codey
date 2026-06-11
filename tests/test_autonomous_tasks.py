import asyncio
import queue
import threading

import pytest

from codey.saas.tasks.asyncio_utils import run_sync_task


async def _return_value(value: str) -> str:
    await asyncio.sleep(0)
    return value


def test_run_async_task_creates_a_loop_for_worker_threads() -> None:
    results: queue.Queue[str] = queue.Queue()

    def worker() -> None:
        results.put(run_sync_task(_return_value("ok")))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert results.get_nowait() == "ok"


def test_run_async_task_rejects_active_event_loops() -> None:
    async def invoke() -> None:
        with pytest.raises(RuntimeError, match="active event loop"):
            run_sync_task(_return_value("nope"))

    asyncio.run(invoke())
