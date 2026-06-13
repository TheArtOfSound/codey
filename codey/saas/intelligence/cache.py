from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import os
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

try:
    import redis.asyncio as aioredis
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-light tests
    if exc.name != "redis":
        raise
    _REDIS_IMPORT_ERROR: ModuleNotFoundError | None = exc

    def _raise_missing_redis(*args, **kwargs):
        raise RuntimeError("redis is required for intelligence cache storage") from _REDIS_IMPORT_ERROR

    class _MissingRedisModule:
        class Redis:
            pass

        from_url = staticmethod(_raise_missing_redis)

    aioredis: Any = _MissingRedisModule()
else:  # pragma: no cover - depends on optional runtime dependency
    _REDIS_IMPORT_ERROR = None

from codey.saas.redis_url import normalize_redis_url

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Default TTLs (seconds)
# ---------------------------------------------------------------------------
TTL_PACKAGE_VERSIONS = 6 * 3600       # 6 hours
TTL_DOCS = 24 * 3600                  # 24 hours
TTL_CVE = 12 * 3600                   # 12 hours
TTL_EMBEDDINGS = 1 * 3600             # 1 hour
TTL_GITHUB_EXAMPLES = 6 * 3600        # 6 hours

_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
REDIS_URL = (
    normalize_redis_url(os.environ.get("REDIS_URL", _DEFAULT_REDIS_URL))
    or _DEFAULT_REDIS_URL
)

# ---------------------------------------------------------------------------
# Connection pool (lazy singleton)
# ---------------------------------------------------------------------------
_pool: aioredis.Redis | None = None


def _require_redis() -> None:
    if _REDIS_IMPORT_ERROR is not None:
        raise RuntimeError("redis is required for intelligence cache storage") from _REDIS_IMPORT_ERROR


async def _get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _require_redis()
        _pool = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
    return _pool


async def close_redis_client(redis_client: Any) -> None:
    """Close a Redis client across redis-py async API variants."""
    aclose = getattr(redis_client, "aclose", None)
    if callable(aclose):
        result = aclose()
        if inspect.isawaitable(result):
            await result
        return

    close = getattr(redis_client, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result
        return

    connection_pool = getattr(redis_client, "connection_pool", None)
    disconnect = getattr(connection_pool, "disconnect", None)
    if not callable(disconnect):
        return

    result = disconnect()
    if inspect.isawaitable(result):
        await result


def _make_key(namespace: str, key: str) -> str:
    """Build a namespaced cache key, hashing long keys for consistency."""
    if len(key) > 200:
        key = hashlib.sha256(key.encode()).hexdigest()
    return f"codey:cache:{namespace}:{key}"


def _json_safe_cache_value(
    value: Any,
    _seen: set[int] | None = None,
) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if _seen is None:
        _seen = set()
    if isinstance(value, dict):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return {
                str(key): _json_safe_cache_value(item, _seen)
                for key, item in value.items()
            }
        finally:
            _seen.remove(value_id)
    if isinstance(value, (set, frozenset)):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return [
                _json_safe_cache_value(item, _seen)
                for item in sorted(
                    value,
                    key=lambda item: (type(item).__name__, repr(item)),
                )
            ]
        finally:
            _seen.remove(value_id)
    if isinstance(value, (list, tuple)):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return [_json_safe_cache_value(item, _seen) for item in value]
        finally:
            _seen.remove(value_id)
    return str(value)


# ---------------------------------------------------------------------------
# Generic cached() wrapper
# ---------------------------------------------------------------------------


async def cached(
    key: str,
    ttl: int,
    fetch_fn: Callable[[], Awaitable[T]],
    *,
    namespace: str = "default",
    force_refresh: bool = False,
) -> T:
    """Return a cached value or call *fetch_fn* and cache the result.

    Parameters
    ----------
    key:
        Unique identifier within the namespace.
    ttl:
        Time-to-live in seconds.
    fetch_fn:
        Async callable that produces the value on cache miss.
    namespace:
        Logical grouping (e.g. ``"package_versions"``).
    force_refresh:
        Bypass the cache and always call *fetch_fn*.
    """
    cache_key = _make_key(namespace, key)
    redis_dependency_available = _REDIS_IMPORT_ERROR is None

    if redis_dependency_available and not force_refresh:
        try:
            r = await _get_redis()

            raw = await r.get(cache_key)
            if raw is not None:
                logger.debug("Cache hit: %s", cache_key)
                return json.loads(raw)

            logger.debug("Cache miss: %s", cache_key)
        except Exception:
            # Redis down — fall through to fetch
            logger.warning("Redis unavailable, skipping cache for %s", cache_key)

    value = await fetch_fn()

    if not redis_dependency_available:
        return value

    try:
        r = await _get_redis()
        await r.set(
            cache_key,
            json.dumps(_json_safe_cache_value(value), default=str, allow_nan=False),
            ex=ttl,
        )
    except Exception:
        logger.warning("Failed to write cache key %s", cache_key)

    return value


# ---------------------------------------------------------------------------
# Convenience wrappers for common namespaces
# ---------------------------------------------------------------------------


async def cached_package_versions(
    key: str, fetch_fn: Callable[[], Awaitable[T]]
) -> T:
    return await cached(key, TTL_PACKAGE_VERSIONS, fetch_fn, namespace="package_versions")


async def cached_docs(
    key: str, fetch_fn: Callable[[], Awaitable[T]]
) -> T:
    return await cached(key, TTL_DOCS, fetch_fn, namespace="docs")


async def cached_cve(
    key: str, fetch_fn: Callable[[], Awaitable[T]]
) -> T:
    return await cached(key, TTL_CVE, fetch_fn, namespace="cve")


async def cached_embeddings(
    key: str, fetch_fn: Callable[[], Awaitable[T]]
) -> T:
    return await cached(key, TTL_EMBEDDINGS, fetch_fn, namespace="embeddings")


async def cached_github_examples(
    key: str, fetch_fn: Callable[[], Awaitable[T]]
) -> T:
    return await cached(key, TTL_GITHUB_EXAMPLES, fetch_fn, namespace="github_examples")


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------


async def invalidate(namespace: str, key: str) -> bool:
    """Delete a specific cache entry. Returns True if the key existed."""
    cache_key = _make_key(namespace, key)
    if _REDIS_IMPORT_ERROR is not None:
        return False

    try:
        r = await _get_redis()
        return bool(await r.delete(cache_key))
    except Exception:
        logger.warning("Failed to invalidate cache key %s", cache_key)
        return False


async def invalidate_namespace(namespace: str) -> int:
    """Delete all entries in a namespace. Returns count deleted."""
    pattern = f"codey:cache:{namespace}:*"
    if _REDIS_IMPORT_ERROR is not None:
        return 0

    try:
        r = await _get_redis()
        deleted = 0
        keys: list[str] = []
        async for key in r.scan_iter(match=pattern, count=500):
            keys.append(key)
            if len(keys) >= 500:
                deleted += int(await r.delete(*keys))
                keys.clear()
        if keys:
            deleted += int(await r.delete(*keys))
        return deleted
    except Exception:
        logger.warning("Failed to invalidate namespace %s", namespace)
        return 0


async def close() -> None:
    """Shut down the Redis connection pool."""
    global _pool
    redis_client = _pool
    _pool = None
    if redis_client is not None:
        await close_redis_client(redis_client)
