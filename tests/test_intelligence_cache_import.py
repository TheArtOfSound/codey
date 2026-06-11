from __future__ import annotations

import asyncio
import builtins
import importlib
import json
import math
import sys

import pytest

import codey.saas.intelligence.cache as cache_module


def test_intelligence_cache_import_does_not_require_redis(monkeypatch) -> None:
    sys.modules.pop("codey.saas.intelligence.cache", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "redis" or name.startswith("redis."):
            raise ModuleNotFoundError("No module named 'redis'", name="redis")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.saas.intelligence.cache")

    async def fetch_value() -> dict[str, str]:
        return {"source": "fallback"}

    assert module._make_key("docs", "abc") == "codey:cache:docs:abc"
    assert asyncio.run(module.cached_docs("abc", fetch_value)) == {"source": "fallback"}
    assert asyncio.run(module.invalidate("docs", "abc")) is False
    with pytest.raises(RuntimeError, match="redis is required for intelligence cache storage"):
        asyncio.run(module._get_redis())


def test_cached_skips_backend_calls_when_redis_dependency_is_missing(monkeypatch) -> None:
    sys.modules.pop("codey.saas.intelligence.cache", None)
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "redis" or name.startswith("redis."):
            raise ModuleNotFoundError("No module named 'redis'", name="redis")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    module = importlib.import_module("codey.saas.intelligence.cache")
    calls = 0

    async def fail_get_redis():
        nonlocal calls
        calls += 1
        raise AssertionError("cache backend should be skipped")

    async def fetch_value() -> dict[str, str]:
        return {"source": "fallback"}

    monkeypatch.setattr(module, "_get_redis", fail_get_redis)

    assert asyncio.run(module.cached_docs("abc", fetch_value)) == {"source": "fallback"}
    assert calls == 0


def test_cache_invalidation_skips_backend_when_redis_dependency_is_missing(
    monkeypatch,
) -> None:
    sys.modules.pop("codey.saas.intelligence.cache", None)
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "redis" or name.startswith("redis."):
            raise ModuleNotFoundError("No module named 'redis'", name="redis")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    module = importlib.import_module("codey.saas.intelligence.cache")
    calls = 0

    async def fail_get_redis():
        nonlocal calls
        calls += 1
        raise AssertionError("cache backend should be skipped")

    monkeypatch.setattr(module, "_get_redis", fail_get_redis)

    assert asyncio.run(module.invalidate("docs", "abc")) is False
    assert asyncio.run(module.invalidate_namespace("docs")) == 0
    assert calls == 0


def test_invalidate_namespace_deletes_scan_results_in_batches(monkeypatch) -> None:
    batches: list[tuple[str, ...]] = []

    class FakeRedis:
        async def scan_iter(self, match, count):
            assert match == "codey:cache:docs:*"
            assert count == 500
            for index in range(501):
                yield f"key-{index}"

        async def delete(self, *keys):
            batches.append(keys)
            return len(keys)

    async def fake_get_redis():
        return FakeRedis()

    monkeypatch.setattr(cache_module, "_REDIS_IMPORT_ERROR", None)
    monkeypatch.setattr(cache_module, "_get_redis", fake_get_redis)

    assert asyncio.run(cache_module.invalidate_namespace("docs")) == 501
    assert [len(batch) for batch in batches] == [500, 1]


def test_cached_force_refresh_skips_prefetch_connection(monkeypatch) -> None:
    connections = 0
    writes: list[str] = []

    class FakeRedis:
        async def get(self, _key):
            raise AssertionError("force_refresh should skip cache reads")

        async def set(self, key, value, ex):
            writes.append(value)
            return True

    async def fake_get_redis():
        nonlocal connections
        connections += 1
        return FakeRedis()

    async def fetch_value() -> dict[str, object]:
        return {
            "source": "refresh",
            "stress": float("inf"),
            "nested": (float("nan"),),
        }

    monkeypatch.setattr(cache_module, "_REDIS_IMPORT_ERROR", None)
    monkeypatch.setattr(cache_module, "_get_redis", fake_get_redis)

    result = asyncio.run(
        cache_module.cached(
            "abc",
            60,
            fetch_value,
            namespace="docs",
            force_refresh=True,
        )
    )

    assert result["source"] == "refresh"
    assert result["stress"] == float("inf")
    assert math.isnan(result["nested"][0])
    assert connections == 1
    assert json.loads(writes[0]) == {
        "source": "refresh",
        "stress": 0.0,
        "nested": [0.0],
    }
    json.dumps(json.loads(writes[0]), allow_nan=False)


def test_cached_force_refresh_serializes_cyclic_payloads(monkeypatch) -> None:
    writes: list[str] = []

    class FakeRedis:
        async def set(self, key, value, ex):
            writes.append(value)
            return True

    async def fake_get_redis():
        return FakeRedis()

    async def fetch_value() -> dict[str, object]:
        cycle: dict[str, object] = {"source": "refresh"}
        cycle["self"] = cycle
        return cycle

    monkeypatch.setattr(cache_module, "_REDIS_IMPORT_ERROR", None)
    monkeypatch.setattr(cache_module, "_get_redis", fake_get_redis)

    result = asyncio.run(
        cache_module.cached(
            "cyclic",
            60,
            fetch_value,
            namespace="docs",
            force_refresh=True,
        )
    )

    assert result["source"] == "refresh"
    assert result["self"] is result
    assert json.loads(writes[0]) == {
        "source": "refresh",
        "self": "[Circular]",
    }


def test_cached_force_refresh_serializes_non_json_edge_values(monkeypatch) -> None:
    writes: list[str] = []

    class _Opaque:
        def __str__(self) -> str:
            return "opaque-value"

    class FakeRedis:
        async def set(self, key, value, ex):
            writes.append(value)
            return True

    async def fake_get_redis():
        return FakeRedis()

    async def fetch_value() -> dict[object, object]:
        return {
            ("tuple", "key"): b"cached-bytes",
            "set_values": {"b", "a"},
            "opaque": _Opaque(),
        }

    monkeypatch.setattr(cache_module, "_REDIS_IMPORT_ERROR", None)
    monkeypatch.setattr(cache_module, "_get_redis", fake_get_redis)

    result = asyncio.run(
        cache_module.cached(
            "edge-values",
            60,
            fetch_value,
            namespace="docs",
            force_refresh=True,
        )
    )

    assert result["set_values"] == {"b", "a"}
    assert json.loads(writes[0]) == {
        "('tuple', 'key')": "cached-bytes",
        "set_values": ["a", "b"],
        "opaque": "opaque-value",
    }
    json.dumps(json.loads(writes[0]), allow_nan=False)
