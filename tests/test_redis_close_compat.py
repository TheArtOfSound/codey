from __future__ import annotations

import pytest

import codey.saas.intelligence.cache as cache_module
from codey.saas.intelligence.cache import close_redis_client


class _AcloseOnlyClient:
    def __init__(self) -> None:
        self.closed_with: str | None = None

    async def aclose(self) -> None:
        self.closed_with = "aclose"


class _SyncAcloseOnlyClient:
    def __init__(self) -> None:
        self.closed_with: str | None = None

    def aclose(self) -> None:
        self.closed_with = "aclose"


class _AsyncCloseOnlyClient:
    def __init__(self) -> None:
        self.closed_with: str | None = None

    async def close(self) -> None:
        self.closed_with = "close"


class _AsyncConnectionPool:
    def __init__(self, client) -> None:
        self._client = client

    async def disconnect(self) -> None:
        self._client.closed_with = "disconnect"


class _SyncConnectionPool:
    def __init__(self, client) -> None:
        self._client = client

    def disconnect(self) -> None:
        self._client.closed_with = "disconnect"


class _AsyncConnectionPoolOnlyClient:
    def __init__(self) -> None:
        self.closed_with: str | None = None
        self.connection_pool = _AsyncConnectionPool(self)


class _SyncConnectionPoolOnlyClient:
    def __init__(self) -> None:
        self.closed_with: str | None = None
        self.connection_pool = _SyncConnectionPool(self)


class _FailingAcloseClient:
    async def aclose(self) -> None:
        raise RuntimeError("close failed")


@pytest.mark.asyncio
async def test_close_redis_client_prefers_aclose() -> None:
    client = _AcloseOnlyClient()

    await close_redis_client(client)

    assert client.closed_with == "aclose"


@pytest.mark.asyncio
async def test_close_redis_client_accepts_sync_aclose() -> None:
    client = _SyncAcloseOnlyClient()

    await close_redis_client(client)

    assert client.closed_with == "aclose"


@pytest.mark.asyncio
async def test_close_redis_client_falls_back_to_close() -> None:
    client = _AsyncCloseOnlyClient()

    await close_redis_client(client)

    assert client.closed_with == "close"


@pytest.mark.asyncio
async def test_close_redis_client_falls_back_to_async_connection_pool() -> None:
    client = _AsyncConnectionPoolOnlyClient()

    await close_redis_client(client)

    assert client.closed_with == "disconnect"


@pytest.mark.asyncio
async def test_close_redis_client_falls_back_to_sync_connection_pool() -> None:
    client = _SyncConnectionPoolOnlyClient()

    await close_redis_client(client)

    assert client.closed_with == "disconnect"


@pytest.mark.asyncio
async def test_close_resets_pool_when_client_close_fails(monkeypatch) -> None:
    client = _FailingAcloseClient()
    monkeypatch.setattr(cache_module, "_pool", client)

    with pytest.raises(RuntimeError, match="close failed"):
        await cache_module.close()

    assert cache_module._pool is None
