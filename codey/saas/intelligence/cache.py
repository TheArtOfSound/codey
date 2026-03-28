from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import redis.asyncio as aioredis

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

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# Connection pool (lazy singleton)
# ---------------------------------------------------------------------------
_pool: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
    return _pool


def _make_key(namespace: str, key: str) -> str:
    """Build a namespaced cache key, hashing long keys for consistency."""
    if len(key) > 200:
        key = hashlib.sha256(key.encode()).hexdigest()
    return f"codey:cache:{namespace}:{key}"


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

    try:
        r = await _get_redis()

        if not force_refresh:
            raw = await r.get(cache_key)
            if raw is not None:
                logger.debug("Cache hit: %s", cache_key)
                return json.loads(raw)

        logger.debug("Cache miss: %s", cache_key)
    except Exception:
        # Redis down — fall through to fetch
        logger.warning("Redis unavailable, skipping cache for %s", cache_key)

    value = await fetch_fn()

    try:
        r = await _get_redis()
        await r.set(cache_key, json.dumps(value, default=str), ex=ttl)
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
    try:
        r = await _get_redis()
        return bool(await r.delete(cache_key))
    except Exception:
        logger.warning("Failed to invalidate cache key %s", cache_key)
        return False


async def invalidate_namespace(namespace: str) -> int:
    """Delete all entries in a namespace. Returns count deleted."""
    pattern = f"codey:cache:{namespace}:*"
    try:
        r = await _get_redis()
        keys = []
        async for key in r.scan_iter(match=pattern, count=500):
            keys.append(key)
        if keys:
            return await r.delete(*keys)
        return 0
    except Exception:
        logger.warning("Failed to invalidate namespace %s", namespace)
        return 0


async def close() -> None:
    """Shut down the Redis connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
