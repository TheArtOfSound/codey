from __future__ import annotations

import json
import logging
import sys
from types import ModuleType, SimpleNamespace

import pytest

import codey.saas.api.app as app_module


class _FailingRedisClient:
    def __init__(self) -> None:
        self.closed = False

    async def ping(self) -> bool:
        raise RuntimeError("redis down")


class _HealthyRedisClient:
    def __init__(self) -> None:
        self.closed = False

    async def ping(self) -> bool:
        return True


class _CredentialErrorRedisClient:
    def __init__(self) -> None:
        self.closed = False

    async def ping(self) -> bool:
        raise RuntimeError(
            "connect rediss://:secret@example.com:6379/0 failed"
        )


@pytest.mark.asyncio
async def test_health_redis_closes_client_after_ping_failure(monkeypatch) -> None:
    client = _FailingRedisClient()

    async def fake_close_redis_client(redis_client) -> None:
        redis_client.closed = True

    redis_asyncio = ModuleType("redis.asyncio")
    redis_asyncio.from_url = lambda *args, **kwargs: client
    redis_pkg = ModuleType("redis")
    redis_pkg.asyncio = redis_asyncio

    monkeypatch.setitem(sys.modules, "redis", redis_pkg)
    monkeypatch.setitem(sys.modules, "redis.asyncio", redis_asyncio)
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.cache",
        SimpleNamespace(close_redis_client=fake_close_redis_client),
    )

    response = await app_module.health_redis()

    assert response.status_code == 503
    assert client.closed is True


@pytest.mark.asyncio
async def test_health_redis_falls_back_to_local_default_when_setting_is_whitespace(
    monkeypatch,
) -> None:
    client = _HealthyRedisClient()
    captured: dict[str, object] = {}

    async def fake_close_redis_client(redis_client) -> None:
        redis_client.closed = True

    def fake_from_url(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return client

    redis_asyncio = ModuleType("redis.asyncio")
    redis_asyncio.from_url = fake_from_url
    redis_pkg = ModuleType("redis")
    redis_pkg.asyncio = redis_asyncio

    monkeypatch.setattr(app_module.settings, "redis_url", "   ")
    monkeypatch.setitem(sys.modules, "redis", redis_pkg)
    monkeypatch.setitem(sys.modules, "redis.asyncio", redis_asyncio)
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.cache",
        SimpleNamespace(close_redis_client=fake_close_redis_client),
    )

    response = await app_module.health_redis()

    assert response.status_code == 200
    assert captured["url"] == "redis://localhost:6379/0"
    assert client.closed is True


def test_health_redis_url_falls_back_to_local_default_when_setting_is_invalid(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        app_module.settings,
        "redis_url",
        "rediss://localhost:not-a-port/0",
    )

    assert app_module._health_redis_url() == "redis://localhost:6379/0"


@pytest.mark.asyncio
async def test_health_redis_normalizes_rediss_urls(monkeypatch) -> None:
    client = _HealthyRedisClient()
    captured: dict[str, object] = {}

    async def fake_close_redis_client(redis_client) -> None:
        redis_client.closed = True

    def fake_from_url(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return client

    redis_asyncio = ModuleType("redis.asyncio")
    redis_asyncio.from_url = fake_from_url
    redis_pkg = ModuleType("redis")
    redis_pkg.asyncio = redis_asyncio

    monkeypatch.setattr(
        app_module.settings,
        "redis_url",
        " rediss://:pass@example.com:6379/0?ssl_cert_reqs= ",
    )
    monkeypatch.setitem(sys.modules, "redis", redis_pkg)
    monkeypatch.setitem(sys.modules, "redis.asyncio", redis_asyncio)
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.cache",
        SimpleNamespace(close_redis_client=fake_close_redis_client),
    )

    response = await app_module.health_redis()

    assert response.status_code == 200
    assert captured["url"] == (
        "rediss://:pass@example.com:6379/0?ssl_cert_reqs=CERT_NONE"
    )
    assert client.closed is True


@pytest.mark.asyncio
async def test_health_redis_cleanup_failure_does_not_mask_response(
    monkeypatch,
    caplog,
) -> None:
    client = _HealthyRedisClient()

    async def fake_close_redis_client(_redis_client) -> None:
        raise RuntimeError(
            "close rediss://user:super-secret@example.com:6379/0"
            "?client_secret=query-secret authorization=Bearer bearer-secret"
        )

    redis_asyncio = ModuleType("redis.asyncio")
    redis_asyncio.from_url = lambda *args, **kwargs: client
    redis_pkg = ModuleType("redis")
    redis_pkg.asyncio = redis_asyncio

    monkeypatch.setitem(sys.modules, "redis", redis_pkg)
    monkeypatch.setitem(sys.modules, "redis.asyncio", redis_asyncio)
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.cache",
        SimpleNamespace(close_redis_client=fake_close_redis_client),
    )
    caplog.set_level(logging.WARNING, logger="codey")

    response = await app_module.health_redis()

    assert response.status_code == 200
    assert "super-secret" not in caplog.text
    assert "query-secret" not in caplog.text
    assert "bearer-secret" not in caplog.text
    assert "rediss://***@example.com:6379/0" in caplog.text
    assert "client_secret=***" in caplog.text
    assert "authorization=Bearer ***" in caplog.text


@pytest.mark.asyncio
async def test_health_redis_redacts_credentials_from_error_payload(monkeypatch) -> None:
    client = _CredentialErrorRedisClient()

    async def fake_close_redis_client(redis_client) -> None:
        redis_client.closed = True

    redis_asyncio = ModuleType("redis.asyncio")
    redis_asyncio.from_url = lambda *args, **kwargs: client
    redis_pkg = ModuleType("redis")
    redis_pkg.asyncio = redis_asyncio

    monkeypatch.setitem(sys.modules, "redis", redis_pkg)
    monkeypatch.setitem(sys.modules, "redis.asyncio", redis_asyncio)
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.cache",
        SimpleNamespace(close_redis_client=fake_close_redis_client),
    )

    response = await app_module.health_redis()
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert "secret" not in payload["redis"]
    assert "rediss://***@example.com:6379/0" in payload["redis"]
    assert client.closed is True
