from __future__ import annotations

import asyncio
import concurrent.futures

import pytest

from codey.llm.code_agent import CodeAgent
import codey.saas.intelligence.providers as providers


class _FakeFuture:
    def __init__(self) -> None:
        self.cancelled = False
        self.requested_timeout: float | None = None

    def result(self, timeout: float | None = None) -> str:
        self.requested_timeout = timeout
        raise concurrent.futures.TimeoutError()

    def cancel(self) -> None:
        self.cancelled = True


class _FakeExecutor:
    last_instance: _FakeExecutor | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.future = _FakeFuture()
        self.shutdown_args: tuple[bool, bool] | None = None
        _FakeExecutor.last_instance = self

    def submit(self, *args, **kwargs) -> _FakeFuture:
        if len(args) > 1:
            close = getattr(args[1], "close", None)
            if callable(close):
                close()
        return self.future

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        self.shutdown_args = (wait, cancel_futures)


def test_call_llm_sync_timeout_raises_timeout_error(monkeypatch) -> None:
    agent = CodeAgent.__new__(CodeAgent)

    async def fake_call_model(*args, **kwargs) -> str:
        return "ok"

    async def fake_wait_for(awaitable, timeout):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(providers, "resolve_model", lambda *_args, **_kwargs: ("stub", "stub"))
    monkeypatch.setattr(providers, "call_model", fake_call_model)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    with pytest.raises(TimeoutError, match="LLM call timed out after 120s"):
        agent._call_llm("system", [])


@pytest.mark.asyncio
async def test_call_llm_async_timeout_cancels_future_and_skips_waiting_shutdown(monkeypatch) -> None:
    agent = CodeAgent.__new__(CodeAgent)

    async def fake_call_model(*args, **kwargs) -> str:
        return "ok"

    monkeypatch.setattr(providers, "resolve_model", lambda *_args, **_kwargs: ("stub", "stub"))
    monkeypatch.setattr(providers, "call_model", fake_call_model)
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", _FakeExecutor)

    with pytest.raises(TimeoutError, match="LLM call timed out after 120s"):
        agent._call_llm("system", [])

    executor = _FakeExecutor.last_instance
    assert executor is not None
    assert executor.future.cancelled is True
    assert executor.shutdown_args == (False, True)
