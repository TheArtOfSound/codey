from __future__ import annotations

import importlib

import pytest
from starlette.requests import Request

from codey.saas.auth.cookies import SESSION_COOKIE_NAME
from codey.saas.auth.jwt import create_access_token
import codey.saas.security.middleware as security_middleware
from codey.saas.security.middleware import SecurityMiddleware


async def _dummy_app(scope, receive, send) -> None:
    return None


def _make_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/boom",
            "headers": headers or [],
            "query_string": b"",
            "scheme": "https",
            "server": ("testserver", 443),
            "client": ("127.0.0.1", 1234),
        }
    )


@pytest.mark.asyncio
async def test_security_headers_applied_to_sanitized_production_errors(
    monkeypatch,
) -> None:
    middleware = SecurityMiddleware(_dummy_app)

    async def fail_call_next(request: Request):
        raise RuntimeError("boom")

    monkeypatch.setattr(security_middleware, "_IS_PRODUCTION", True)

    response = await middleware.dispatch(_make_request(), fail_call_next)

    assert response.status_code == 500
    assert response.headers["Content-Security-Policy"] == security_middleware._CSP
    assert response.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_module_detects_whitespace_padded_production_env(monkeypatch) -> None:
    monkeypatch.setenv("CODEY_ENV", " production ")

    reloaded = importlib.reload(security_middleware)

    assert reloaded._coerce_non_empty_security_env_text(" production ") == "production"
    assert reloaded._IS_PRODUCTION is True


def test_module_rejects_control_character_production_env(monkeypatch) -> None:
    monkeypatch.setenv("CODEY_ENV", "prod\tuction")

    reloaded = importlib.reload(security_middleware)

    assert reloaded._coerce_non_empty_security_env_text("prod\tuction") is None
    assert reloaded._IS_PRODUCTION is False


def test_extract_user_id_accepts_session_cookie_token() -> None:
    token = create_access_token("user-1")
    request = _make_request(
        headers=[(b"cookie", f"{SESSION_COOKIE_NAME}={token}".encode())]
    )

    assert SecurityMiddleware._extract_user_id(request) == "user-1"


def test_extract_user_id_accepts_loose_bearer_header_and_trimmed_subject(monkeypatch) -> None:
    request = _make_request(
        headers=[(b"authorization", b"  bearer   access-token  ")],
    )

    def fake_decode_access_token(token: str) -> dict[str, str]:
        assert token == "access-token"
        return {"sub": " user-1 "}

    monkeypatch.setattr("codey.saas.auth.jwt.decode_access_token", fake_decode_access_token)

    assert SecurityMiddleware._extract_user_id(request) == "user-1"
