from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, Response, status

# ---------------------------------------------------------------------------
# Default rate-limit tiers
# ---------------------------------------------------------------------------

DEFAULT_LIMITS: dict[str, dict] = {
    "login": {"max_requests": 5, "window_seconds": 15 * 60},
    "password_reset": {"max_requests": 3, "window_seconds": 60 * 60},
    "api_general": {"max_requests": 1000, "window_seconds": 60 * 60},
    "session_create": {"max_requests": 20, "window_seconds": 60 * 60},
    "file_upload": {"max_requests": 10, "window_seconds": 60 * 60},
}


# ---------------------------------------------------------------------------
# Token-bucket implementation (in-memory)
# ---------------------------------------------------------------------------


@dataclass
class _Bucket:
    """A single token-bucket for one (key, category) pair."""

    tokens: float
    max_tokens: int
    refill_rate: float  # tokens per second
    last_refill: float = field(default_factory=time.monotonic)

    def refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def consume(self) -> bool:
        """Try to consume one token.  Returns ``True`` if allowed."""
        self.refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def remaining(self) -> int:
        self.refill()
        return int(self.tokens)


class RateLimiter:
    """In-memory token-bucket rate limiter.

    For production deployments behind multiple workers, swap the backing store
    for Redis (e.g. via ``aioredis``).  The public interface stays identical.
    """

    def __init__(self, limits: dict[str, dict] | None = None) -> None:
        self._limits: dict[str, dict] = limits or DEFAULT_LIMITS
        # Composite key: ``"{category}:{key}"`` -> Bucket
        self._buckets: dict[str, _Bucket] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check(self, key: str, category: str) -> bool:
        """Return ``True`` if the request is allowed, ``False`` if rate-limited."""
        bucket = self._get_or_create(key, category)
        return bucket.consume()

    async def get_remaining(self, key: str, category: str) -> int:
        """Return the number of requests still available in the current window."""
        bucket = self._get_or_create(key, category)
        return bucket.remaining()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_or_create(self, key: str, category: str) -> _Bucket:
        composite = f"{category}:{key}"
        if composite not in self._buckets:
            cfg = self._limits.get(category)
            if cfg is None:
                raise ValueError(f"Unknown rate-limit category: {category!r}")
            max_requests: int = cfg["max_requests"]
            window_seconds: int = cfg["window_seconds"]
            refill_rate = max_requests / window_seconds
            self._buckets[composite] = _Bucket(
                tokens=float(max_requests),
                max_tokens=max_requests,
                refill_rate=refill_rate,
            )
        return self._buckets[composite]


# ---------------------------------------------------------------------------
# Singleton — importable throughout the application
# ---------------------------------------------------------------------------

_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Return the module-level :class:`RateLimiter` singleton."""
    return _limiter


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def rate_limit(category: str):
    """Return a FastAPI dependency that enforces rate limiting.

    Usage::

        @router.post("/login")
        async def login(
            ...,
            _rl: None = Depends(rate_limit("login")),
        ):
            ...

    The dependency identifies the caller by user ID (from JWT ``sub`` claim)
    when available, falling back to the client IP address.  It sets standard
    ``X-RateLimit-*`` response headers and raises ``HTTPException(429)`` when
    the limit is exceeded.
    """

    async def _dependency(request: Request, response: Response) -> None:
        limiter = get_rate_limiter()

        # Determine rate-limit key: prefer authenticated user, fall back to IP.
        key: str | None = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            # Lightweight JWT peek — import lazily to avoid circular deps.
            try:
                from codey.saas.auth.jwt import decode_access_token

                payload = decode_access_token(auth_header.split(" ", 1)[1])
                key = payload.get("sub")
            except Exception:
                pass  # token invalid; fall through to IP-based limiting

        if key is None:
            key = request.client.host if request.client else "unknown"

        cfg = limiter._limits.get(category)
        if cfg is None:
            raise ValueError(f"Unknown rate-limit category: {category!r}")

        allowed = await limiter.check(key, category)
        remaining = await limiter.get_remaining(key, category)

        # Set informational headers regardless of outcome.
        response.headers["X-RateLimit-Limit"] = str(cfg["max_requests"])
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(cfg["window_seconds"])

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {category}. Try again later.",
                headers={
                    "X-RateLimit-Limit": str(cfg["max_requests"]),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(cfg["window_seconds"]),
                    "Retry-After": str(cfg["window_seconds"]),
                },
            )

    return _dependency
