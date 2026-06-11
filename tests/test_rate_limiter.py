from __future__ import annotations

from types import SimpleNamespace

import pytest

from codey.saas.auth.cookies import SESSION_COOKIE_NAME
from codey.saas.auth.jwt import create_access_token
from codey.saas.security import rate_limiter as rate_limiter_module
from codey.saas.security.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_prunes_stale_buckets(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(rate_limiter_module.time, "monotonic", lambda: now[0])

    limiter = RateLimiter(
        {"login": {"max_requests": 5, "window_seconds": 60}},
        prune_interval_seconds=0.0,
    )

    assert await limiter.check("user-1", "login") is True
    assert "login:user-1" in limiter._buckets

    now[0] += 61.0

    assert await limiter.check("user-2", "login") is True
    assert "login:user-1" not in limiter._buckets
    assert "login:user-2" in limiter._buckets


@pytest.mark.asyncio
async def test_rate_limiter_keeps_recent_buckets(monkeypatch) -> None:
    now = [200.0]
    monkeypatch.setattr(rate_limiter_module.time, "monotonic", lambda: now[0])

    limiter = RateLimiter(
        {"login": {"max_requests": 5, "window_seconds": 60}},
        prune_interval_seconds=0.0,
    )

    assert await limiter.check("user-1", "login") is True

    now[0] += 30.0

    assert await limiter.check("user-2", "login") is True
    assert "login:user-1" in limiter._buckets
    assert "login:user-2" in limiter._buckets


@pytest.mark.asyncio
async def test_rate_limiter_normalizes_malformed_limit_config() -> None:
    limiter = RateLimiter(
        {"login": {"max_requests": True, "window_seconds": 0}},
        prune_interval_seconds=0.0,
    )

    assert await limiter.check("user-1", "login") is True
    assert await limiter.get_remaining("user-1", "login") == 0


@pytest.mark.asyncio
async def test_rate_limiter_caps_extreme_limit_config() -> None:
    limiter = RateLimiter(
        {
            "login": {
                "max_requests": 10**10000,
                "window_seconds": 10**10000,
            }
        },
        prune_interval_seconds=0.0,
    )

    assert await limiter.check("user-1", "login") is True
    bucket = limiter._buckets["login:user-1"]
    assert bucket.max_tokens == rate_limiter_module._MAX_LIMIT_VALUE
    assert bucket.stale_after_seconds == float(rate_limiter_module._MAX_LIMIT_VALUE)


@pytest.mark.asyncio
async def test_rate_limit_dependency_uses_session_cookie_subject(monkeypatch) -> None:
    limiter = RateLimiter(
        {"login": {"max_requests": 5, "window_seconds": 60}},
        prune_interval_seconds=0.0,
    )
    token = create_access_token("user-1")
    request = SimpleNamespace(
        headers={},
        cookies={SESSION_COOKIE_NAME: token},
        client=SimpleNamespace(host="203.0.113.10"),
    )
    response = SimpleNamespace(headers={})

    monkeypatch.setattr(rate_limiter_module, "_limiter", limiter)

    dependency = rate_limiter_module.rate_limit("login")
    await dependency(request, response)

    assert "login:user-1" in limiter._buckets
    assert "login:203.0.113.10" not in limiter._buckets
    assert response.headers == {
        "X-RateLimit-Limit": "5",
        "X-RateLimit-Remaining": "4",
        "X-RateLimit-Reset": "60",
    }


def test_authenticated_rate_limit_key_normalizes_subject(monkeypatch) -> None:
    monkeypatch.setattr(
        "codey.saas.auth.jwt.decode_access_token",
        lambda _token: {"sub": " user-1 "},
    )
    request = SimpleNamespace(
        headers={"authorization": "Bearer token"},
        cookies={},
        client=SimpleNamespace(host="203.0.113.10"),
    )

    assert rate_limiter_module._authenticated_rate_limit_key(request) == "user-1"


def test_authenticated_rate_limit_key_accepts_case_insensitive_bearer_scheme(
    monkeypatch,
) -> None:
    seen_tokens: list[str] = []

    def fake_decode(token: str) -> dict[str, str]:
        seen_tokens.append(token)
        return {"sub": "user-1"}

    monkeypatch.setattr("codey.saas.auth.jwt.decode_access_token", fake_decode)
    request = SimpleNamespace(
        headers={"authorization": "  bearer   token  "},
        cookies={},
        client=SimpleNamespace(host="203.0.113.10"),
    )

    assert rate_limiter_module._authenticated_rate_limit_key(request) == "user-1"
    assert seen_tokens == ["token"]


@pytest.mark.asyncio
async def test_rate_limit_dependency_reports_normalized_malformed_config(
    monkeypatch,
) -> None:
    limiter = RateLimiter(
        {"login": {"max_requests": True, "window_seconds": 0}},
        prune_interval_seconds=0.0,
    )
    request = SimpleNamespace(
        headers={},
        cookies={},
        client=SimpleNamespace(host="203.0.113.10"),
    )
    response = SimpleNamespace(headers={})

    monkeypatch.setattr(rate_limiter_module, "_limiter", limiter)

    dependency = rate_limiter_module.rate_limit("login")
    await dependency(request, response)

    assert response.headers == {
        "X-RateLimit-Limit": "1",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": "1",
    }

    with pytest.raises(rate_limiter_module.HTTPException) as exc_info:
        await dependency(request, response)

    assert exc_info.value.headers == {
        "X-RateLimit-Limit": "1",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": "1",
        "Retry-After": "1",
    }
