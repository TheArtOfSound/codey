from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, Response, status

from codey.saas.auth.cookies import SESSION_COOKIE_NAME

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
_MAX_LIMIT_VALUE = 1_000_000


def _coerce_limit_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return min(_MAX_LIMIT_VALUE, max(1, parsed))


# ---------------------------------------------------------------------------
# Token-bucket implementation (in-memory)
# ---------------------------------------------------------------------------


@dataclass
class _Bucket:
    """A single token-bucket for one (key, category) pair."""

    tokens: float
    max_tokens: int
    refill_rate: float  # tokens per second
    stale_after_seconds: float
    last_refill: float = field(default_factory=lambda: time.monotonic())

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

    def is_stale(self, now: float) -> bool:
        return (now - self.last_refill) >= self.stale_after_seconds


class RateLimiter:
    """In-memory token-bucket rate limiter.

    For production deployments behind multiple workers, swap the backing store
    for Redis (e.g. via ``aioredis``).  The public interface stays identical.
    """

    def __init__(
        self,
        limits: dict[str, dict] | None = None,
        *,
        prune_interval_seconds: float = 60.0,
    ) -> None:
        self._limits: dict[str, dict] = limits or DEFAULT_LIMITS
        # Composite key: ``"{category}:{key}"`` -> Bucket
        self._buckets: dict[str, _Bucket] = {}
        self._prune_interval_seconds = max(prune_interval_seconds, 0.0)
        self._last_prune = time.monotonic()

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
        self._maybe_prune()
        composite = f"{category}:{key}"
        if composite not in self._buckets:
            cfg = self._limits.get(category)
            if cfg is None:
                raise ValueError(f"Unknown rate-limit category: {category!r}")
            max_requests = _coerce_limit_int(cfg.get("max_requests"), 1)
            window_seconds = _coerce_limit_int(cfg.get("window_seconds"), 60)
            refill_rate = max_requests / window_seconds
            self._buckets[composite] = _Bucket(
                tokens=float(max_requests),
                max_tokens=max_requests,
                refill_rate=refill_rate,
                stale_after_seconds=float(window_seconds),
            )
        return self._buckets[composite]

    def _maybe_prune(self) -> None:
        now = time.monotonic()
        if (now - self._last_prune) < self._prune_interval_seconds:
            return
        self._last_prune = now
        stale_keys = [
            composite
            for composite, bucket in self._buckets.items()
            if bucket.is_stale(now)
        ]
        for composite in stale_keys:
            self._buckets.pop(composite, None)


# ---------------------------------------------------------------------------
# Singleton — importable throughout the application
# ---------------------------------------------------------------------------

_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Return the module-level :class:`RateLimiter` singleton."""
    return _limiter


def _authenticated_rate_limit_key(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    candidates: list[object] = []
    if isinstance(auth_header, str):
        auth_parts = auth_header.strip().split(None, 1)
        if len(auth_parts) == 2 and auth_parts[0].lower() == "bearer":
            candidates.append(auth_parts[1])
    candidates.append(request.cookies.get(SESSION_COOKIE_NAME))

    try:
        from codey.saas.auth.jwt import decode_access_token, normalize_access_token_candidate
    except Exception:
        return None

    for candidate in candidates:
        normalized = normalize_access_token_candidate(candidate)
        if normalized is None:
            continue
        try:
            payload = decode_access_token(normalized)
        except Exception:
            continue
        subject = normalize_access_token_candidate(payload.get("sub"))
        if subject is not None:
            return subject

    return None


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
        key = _authenticated_rate_limit_key(request)
        if key is None:
            key = request.client.host if request.client else "unknown"

        cfg = limiter._limits.get(category)
        if cfg is None:
            raise ValueError(f"Unknown rate-limit category: {category!r}")
        max_requests = _coerce_limit_int(cfg.get("max_requests"), 1)
        window_seconds = _coerce_limit_int(cfg.get("window_seconds"), 60)

        allowed = await limiter.check(key, category)
        remaining = await limiter.get_remaining(key, category)

        # Set informational headers regardless of outcome.
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(window_seconds)

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {category}. Try again later.",
                headers={
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(window_seconds),
                    "Retry-After": str(window_seconds),
                },
            )

    return _dependency
