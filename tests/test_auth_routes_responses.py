from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import codey.saas.api.auth_routes as auth_routes
import pytest
from starlette.requests import Request
from starlette.responses import Response


def test_user_to_response_tolerates_string_created_at() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="Repo User",
        avatar_url=None,
        github_id=None,
        github_token=None,
        plan="free",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        created_at=" 2026-01-02T03:04:05Z ",
    )

    response = auth_routes._user_to_response(user)

    assert response.created_at == "2026-01-02T03:04:05Z"


def test_user_to_response_coerces_malformed_profile_fields() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name=["Repo User"],
        avatar_url={"url": "https://example.com/avatar.png"},
        github_id=None,
        github_token=None,
        plan=["pro"],
        plan_status={"state": "active"},
        credits_remaining="10",
        topup_credits={"value": 2},
        total_credits=["12"],
        created_at="2026-01-02T03:04:05Z",
    )

    response = auth_routes._user_to_response(user)

    assert response.name is None
    assert response.avatar_url is None
    assert response.plan == "free"
    assert response.plan_status == "active"
    assert response.credits_remaining == 10
    assert response.topup_credits == 0
    assert response.total_credits == 10


def test_user_to_response_allows_safe_avatar_url_with_query() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="Repo User",
        avatar_url=" https://avatars.example.com/u/1?v=4 ",
        github_id=None,
        github_token=None,
        plan="free",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        created_at="2026-01-02T03:04:05Z",
    )

    response = auth_routes._user_to_response(user)

    assert response.avatar_url == "https://avatars.example.com/u/1?v=4"


@pytest.mark.parametrize(
    "avatar_url",
    [
        "javascript:alert(1)",
        "https://user:secret@avatars.example.com/u/1",
        "https://avatars.example.com/u/1?access_token=secret",
        "https://avatars.example.com/u/1#client_secret=secret",
        "https://avatars.example.com:not-a-port/u/1",
        "https:///u/1",
        "https://avatars.example.com/u/1\r\nbad",
    ],
)
def test_user_to_response_rejects_unsafe_avatar_urls(avatar_url: str) -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="Repo User",
        avatar_url=avatar_url,
        github_id=None,
        github_token=None,
        plan="free",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        created_at="2026-01-02T03:04:05Z",
    )

    response = auth_routes._user_to_response(user)

    assert response.avatar_url is None


def test_user_to_response_coerces_malformed_email() -> None:
    user = SimpleNamespace(
        id="user-1",
        email=["user@example.com"],
        name="Repo User",
        avatar_url=None,
        github_id=None,
        github_token=None,
        plan="free",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        created_at="2026-01-02T03:04:05Z",
    )

    response = auth_routes._user_to_response(user)

    assert response.email == ""


def test_user_to_response_tolerates_missing_legacy_fields() -> None:
    response = auth_routes._user_to_response(SimpleNamespace(id="user-2"))

    assert response.id == "user-2"
    assert response.email == ""
    assert response.name is None
    assert response.avatar_url is None
    assert response.github_connected is False
    assert response.plan == "free"
    assert response.plan_status == "active"
    assert response.credits_remaining == 0
    assert response.topup_credits == 0
    assert response.total_credits == 0
    assert response.created_at == ""


def test_user_to_response_ignores_malformed_github_connection_fields() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="Repo User",
        avatar_url=None,
        github_id={"id": "123"},
        github_token=["gh-token"],
        plan="free",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        created_at="2026-01-02T03:04:05Z",
    )

    response = auth_routes._user_to_response(user)

    assert response.github_connected is False


def test_user_to_response_rejects_line_break_github_tokens() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="Repo User",
        avatar_url=None,
        github_id=None,
        github_token="ghp_validprefix\nInjected: header",
        plan="free",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        created_at="2026-01-02T03:04:05Z",
    )

    response = auth_routes._user_to_response(user)

    assert response.github_connected is False


def test_user_to_response_rejects_ascii_control_github_tokens() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="Repo User",
        avatar_url=None,
        github_id=None,
        github_token="ghp_validprefix\tInjected",
        plan="free",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        created_at="2026-01-02T03:04:05Z",
    )

    response = auth_routes._user_to_response(user)

    assert response.github_connected is False


def test_user_to_response_rejects_internal_whitespace_github_tokens() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="Repo User",
        avatar_url=None,
        github_id=None,
        github_token="ghp_validprefix bad",
        plan="free",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        created_at="2026-01-02T03:04:05Z",
    )

    response = auth_routes._user_to_response(user)

    assert response.github_connected is False


def test_auth_int_coercion_rejects_non_finite_values() -> None:
    assert auth_routes._coerce_auth_int(float("nan"), fallback=-1) == -1
    assert auth_routes._coerce_auth_int(float("inf"), fallback=-1) == -1
    assert auth_routes._coerce_auth_int("-inf", fallback=-1) == -1
    assert auth_routes._coerce_auth_int("3", fallback=-1) == 3


def test_redact_auth_error_hides_common_secret_shapes() -> None:
    message = auth_routes._redact_auth_error(
        "oauth failed https://user:url-secret@example.test/callback"
        "?refresh_token=query-secret authorization=Bearer bearer-secret "
        "for operator@example.test",
    )

    assert "url-secret" not in message
    assert "query-secret" not in message
    assert "bearer-secret" not in message
    assert "operator@example.test" not in message
    assert "https://***@example.test/callback" in message
    assert "refresh_token=***" in message
    assert "authorization=Bearer ***" in message
    assert "[redacted-email]" in message


def test_github_oauth_configured_rejects_whitespace_credentials(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.settings, "github_client_id", "   ")
    monkeypatch.setattr(auth_routes.settings, "github_client_secret", "gh-secret")

    assert auth_routes._github_oauth_configured() is False


def test_google_oauth_configured_rejects_whitespace_credentials(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.settings, "google_client_id", "google-client")
    monkeypatch.setattr(auth_routes.settings, "google_client_secret", "   ")

    assert auth_routes._google_oauth_configured() is False


def test_browser_callback_redirect_trims_frontend_origin() -> None:
    redirect_url = auth_routes._browser_callback_redirect(
        "github",
        "state-123",
        frontend_origin=" https://app.example.com/ ",
    )

    parsed = urlparse(redirect_url)
    assert (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        == "https://app.example.com/auth/callback"
    )
    assert parse_qs(parsed.query) == {
        "provider": ["github"],
        "state": ["state-123"],
        "auth_complete": ["1"],
    }


def test_browser_callback_redirect_rejects_whitespace_settings_fallback(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.settings, "frontend_url", "   ")

    redirect_url = auth_routes._browser_callback_redirect("google", "state-456")

    assert redirect_url == "/auth/callback?provider=google&state=state-456&auth_complete=1"


def test_browser_callback_redirect_rejects_malformed_settings_fallback(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.settings, "frontend_url", "app.example.com")

    redirect_url = auth_routes._browser_callback_redirect("github", "state-123")

    assert redirect_url == "/auth/callback?provider=github&state=state-123&auth_complete=1"


def test_browser_callback_redirect_rejects_credentialed_frontend_origin(
    monkeypatch,
) -> None:
    monkeypatch.setattr(auth_routes.settings, "frontend_url", "   ")

    redirect_url = auth_routes._browser_callback_redirect(
        "github",
        "state-123",
        frontend_origin="https://user:pass@app.example.com",
    )

    assert redirect_url == "/auth/callback?provider=github&state=state-123&auth_complete=1"


def test_browser_callback_redirect_rejects_line_break_frontend_origin(
    monkeypatch,
) -> None:
    monkeypatch.setattr(auth_routes.settings, "frontend_url", "   ")

    redirect_url = auth_routes._browser_callback_redirect(
        "github",
        "state-123",
        frontend_origin="https://app.example.com\nSet-Cookie: bad=1",
    )

    assert redirect_url == "/auth/callback?provider=github&state=state-123&auth_complete=1"


def test_browser_callback_redirect_rejects_internal_whitespace_frontend_origin(
    monkeypatch,
) -> None:
    monkeypatch.setattr(auth_routes.settings, "frontend_url", "   ")

    redirect_url = auth_routes._browser_callback_redirect(
        "github",
        "state-123",
        frontend_origin="https://app example.com",
    )

    assert redirect_url == "/auth/callback?provider=github&state=state-123&auth_complete=1"


def test_browser_callback_redirect_rejects_zero_port_frontend_origin(
    monkeypatch,
) -> None:
    monkeypatch.setattr(auth_routes.settings, "frontend_url", "   ")

    redirect_url = auth_routes._browser_callback_redirect(
        "github",
        "state-123",
        frontend_origin="https://app.example.com:0",
    )

    assert redirect_url == "/auth/callback?provider=github&state=state-123&auth_complete=1"


def test_browser_callback_redirect_rejects_ascii_control_frontend_origin(
    monkeypatch,
) -> None:
    monkeypatch.setattr(auth_routes.settings, "frontend_url", "   ")

    redirect_url = auth_routes._browser_callback_redirect(
        "github",
        "state-123",
        frontend_origin="https://app.example.com/\x00evil",
    )

    assert redirect_url == "/auth/callback?provider=github&state=state-123&auth_complete=1"


def test_resolved_oauth_callback_frontend_origin_falls_back_to_request_header() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-codey-frontend-origin", b" https://app.example.com "),
            ],
        }
    )

    resolved = auth_routes._resolved_oauth_callback_frontend_origin(
        request,
        {"frontend_origin": "   "},
    )

    assert resolved == "https://app.example.com"


def test_resolved_oauth_callback_frontend_origin_rejects_zero_port_state() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-codey-frontend-origin", b" https://app.example.com "),
            ],
        }
    )

    resolved = auth_routes._resolved_oauth_callback_frontend_origin(
        request,
        {"frontend_origin": "https://state.example.com:0"},
    )

    assert resolved == "https://app.example.com"


def test_resolved_oauth_callback_api_base_url_falls_back_to_request_header() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-codey-api-base-url", b" https://api.example.com/proxy/ "),
            ],
        }
    )

    resolved = auth_routes._resolved_oauth_callback_api_base_url(
        request,
        {"api_base_url": "api.example.com"},
    )

    assert resolved == "https://api.example.com/proxy"


def test_resolved_oauth_callback_api_base_url_rejects_zero_port_state() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-codey-api-base-url", b" https://api.example.com/proxy/ "),
            ],
        }
    )

    resolved = auth_routes._resolved_oauth_callback_api_base_url(
        request,
        {"api_base_url": "https://state-api.example.com:0/proxy"},
    )

    assert resolved == "https://api.example.com/proxy"


def test_resolved_oauth_callback_api_base_url_rejects_ascii_control_state() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-codey-api-base-url", b" https://api.example.com/proxy/ "),
            ],
        }
    )

    resolved = auth_routes._resolved_oauth_callback_api_base_url(
        request,
        {"api_base_url": "https://api.example.com/proxy\x00evil"},
    )

    assert resolved == "https://api.example.com/proxy"


def test_resolved_oauth_callback_api_base_url_rejects_line_break_state() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-codey-api-base-url", b" https://api.example.com/proxy/ "),
            ],
        }
    )

    resolved = auth_routes._resolved_oauth_callback_api_base_url(
        request,
        {"api_base_url": "https://api.example.com/proxy\nX-Bad: 1"},
    )

    assert resolved == "https://api.example.com/proxy"


def test_resolved_oauth_callback_api_base_url_rejects_traversal_state() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-codey-api-base-url", b" https://api.example.com/proxy/ "),
            ],
        }
    )

    resolved = auth_routes._resolved_oauth_callback_api_base_url(
        request,
        {"api_base_url": "https://api.example.com/proxy/%2e%2e/admin"},
    )

    assert resolved == "https://api.example.com/proxy"


def test_resolved_oauth_callback_api_base_url_rejects_encoded_backslash_traversal_state() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-codey-api-base-url", b" https://api.example.com/proxy/ "),
            ],
        }
    )

    resolved = auth_routes._resolved_oauth_callback_api_base_url(
        request,
        {"api_base_url": "https://api.example.com/proxy/%5c..%5cadmin"},
    )

    assert resolved == "https://api.example.com/proxy"


@pytest.mark.asyncio
async def test_signup_uses_request_frontend_origin_for_welcome_email(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeAuthService:
        def __init__(self, db: object) -> None:
            captured["db"] = db

        async def signup(
            self,
            *,
            email: str,
            password: str,
            name: str | None = None,
            frontend_origin: str | None = None,
        ):
            captured["email"] = email
            captured["password"] = password
            captured["name"] = name
            captured["frontend_origin"] = frontend_origin
            return (
                SimpleNamespace(
                    id="user-1",
                    email=email,
                    name=name,
                    avatar_url=None,
                    github_id=None,
                    github_token=None,
                    plan="free",
                    plan_status="active",
                    credits_remaining=10,
                    topup_credits=0,
                    total_credits=10,
                    created_at="2026-01-02T03:04:05Z",
                ),
                "signup-token",
            )

    def fake_set_auth_cookie(
        response: Response,
        token: str,
        *,
        frontend_origin: str | None = None,
        api_base_url: str | None = None,
    ) -> None:
        captured["cookie_token"] = token
        captured["cookie_frontend_origin"] = frontend_origin
        captured["cookie_api_base_url"] = api_base_url

    monkeypatch.setattr(auth_routes, "AuthService", _FakeAuthService)
    monkeypatch.setattr(auth_routes, "set_auth_cookie", fake_set_auth_cookie)

    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-codey-frontend-origin", b" https://app.example.com "),
                (b"x-codey-api-base-url", b" https://api.example.com/proxy/ "),
            ],
        }
    )
    response = Response()

    result = await auth_routes.signup(
        auth_routes.SignupRequest(
            email="user@example.com",
            password="correct horse battery staple",
            name="Repo User",
        ),
        request,
        response,
        db=object(),
    )

    assert captured["frontend_origin"] == "https://app.example.com"
    assert captured["cookie_frontend_origin"] == "https://app.example.com"
    assert captured["cookie_api_base_url"] == "https://api.example.com/proxy"
    assert result.token == "signup-token"
