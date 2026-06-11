from __future__ import annotations

import pytest
from fastapi import HTTPException, status

import codey.saas.auth.oauth as oauth


class _FakeResponse:
    def __init__(self, payload) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _InvalidJsonResponse(_FakeResponse):
    def json(self):
        raise ValueError("invalid json")


class _GitHubAsyncClient:
    last_timeout: float | None = None
    last_post_data: dict | None = None

    def __init__(self, *args, timeout: float | None = None, **kwargs) -> None:
        _GitHubAsyncClient.last_timeout = timeout

    async def __aenter__(self) -> _GitHubAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        _GitHubAsyncClient.last_post_data = data
        return _FakeResponse({"access_token": "gh-token"})

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == oauth._GITHUB_USER_URL:
            return _FakeResponse(
                {
                    "id": 123,
                    "email": "user@example.com",
                    "name": "User",
                    "login": "user",
                    "avatar_url": "https://example.com/avatar.png",
                }
            )
        return _FakeResponse([])


class _GoogleTimeoutAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        raise oauth.httpx.TimeoutException("timed out")


class _GitHubUnsafeAvatarAsyncClient(_GitHubAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == oauth._GITHUB_USER_URL:
            return _FakeResponse(
                {
                    "id": 123,
                    "email": "user@example.com",
                    "name": "User",
                    "login": "user",
                    "avatar_url": "https://example.com/avatar.png?access_token=secret",
                }
            )
        return _FakeResponse([])


def test_coerce_oauth_bearer_token_rejects_ascii_controls() -> None:
    assert oauth._coerce_oauth_bearer_token(" gh-token ") == "gh-token"
    assert oauth._coerce_oauth_bearer_token("gh-token\tbad") is None
    assert oauth._coerce_oauth_bearer_token("gh-token\x7fbad") is None
    assert oauth._coerce_oauth_bearer_token("gh-token bad") is None


def test_coerce_non_empty_oauth_text_rejects_ascii_controls() -> None:
    assert oauth._coerce_non_empty_oauth_text(" value ") == "value"
    assert oauth._coerce_non_empty_oauth_text("value\nbad") is None
    assert oauth._coerce_non_empty_oauth_text("value\x7fbad") is None


def test_coerce_oauth_avatar_url_rejects_unsafe_urls() -> None:
    assert (
        oauth._coerce_oauth_avatar_url(" https://example.com/avatar.png?v=4 ")
        == "https://example.com/avatar.png?v=4"
    )
    assert oauth._coerce_oauth_avatar_url("javascript:alert(1)") is None
    assert (
        oauth._coerce_oauth_avatar_url("https://user:secret@example.com/avatar.png")
        is None
    )
    assert (
        oauth._coerce_oauth_avatar_url(
            "https://example.com/avatar.png?access_token=secret"
        )
        is None
    )
    assert (
        oauth._coerce_oauth_avatar_url(
            "https://example.com/avatar.png#client_secret=secret"
        )
        is None
    )


def test_coerce_oauth_bool_rejects_malformed_numeric_values() -> None:
    assert oauth._coerce_oauth_bool(True) is True
    assert oauth._coerce_oauth_bool(1) is True
    assert oauth._coerce_oauth_bool(0) is False
    assert oauth._coerce_oauth_bool("true") is True
    assert oauth._coerce_oauth_bool("false") is False
    assert oauth._coerce_oauth_bool(2) is False
    assert oauth._coerce_oauth_bool(-1) is False
    assert oauth._coerce_oauth_bool(0.5) is False
    assert oauth._coerce_oauth_bool(float("nan")) is False
    assert oauth._coerce_oauth_bool(float("inf")) is False


class _GitHubServerErrorAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        request = oauth.httpx.Request("POST", url)
        raise oauth.httpx.HTTPStatusError(
            "server error",
            request=request,
            response=oauth.httpx.Response(500, request=request),
        )


class _GitHubRequestErrorAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        raise oauth.httpx.ConnectError(
            "network unreachable",
            request=oauth.httpx.Request("POST", url),
        )


class _GitHubInvalidUserPayloadAsyncClient(_GitHubAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == oauth._GITHUB_USER_URL:
            return _FakeResponse(["not-a-dict"])
        return _FakeResponse([])


class _GitHubBlankTokenAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        return _FakeResponse({"access_token": "   "})

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        raise AssertionError("GitHub user lookup should not run for a blank access token")


class _GitHubMalformedTokenAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        return _FakeResponse({"access_token": "gh-token\nInjected: header"})

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        raise AssertionError("GitHub user lookup should not run for a malformed access token")


class _GitHubInvalidJsonAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        return _InvalidJsonResponse(None)

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        raise AssertionError("GitHub user lookup should not run for invalid JSON")


class _GitHubWhitespaceUserFieldsAsyncClient(_GitHubAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == oauth._GITHUB_USER_URL:
            return _FakeResponse(
                {
                    "id": 123,
                    "email": "   ",
                    "name": "   ",
                    "login": "user",
                    "avatar_url": "   ",
                }
            )
        return _FakeResponse(
            [
                {"email": "   ", "primary": True, "verified": True},
            ]
        )


class _GitHubWhitespaceUserIdAsyncClient(_GitHubAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == oauth._GITHUB_USER_URL:
            return _FakeResponse(
                {
                    "id": " 123 ",
                    "email": "user@example.com",
                    "name": "User",
                    "login": "user",
                    "avatar_url": None,
                }
            )
        return _FakeResponse([])


class _GitHubStringEmailFlagsAsyncClient(_GitHubAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == oauth._GITHUB_USER_URL:
            return _FakeResponse(
                {
                    "id": 123,
                    "email": None,
                    "name": "User",
                    "login": "user",
                    "avatar_url": "https://example.com/avatar.png",
                }
            )
        return _FakeResponse(
            [
                {"email": "wrong@example.com", "primary": "false", "verified": "true"},
                {"email": "right@example.com", "primary": "true", "verified": "true"},
            ]
        )


class _GoogleServerErrorAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        request = oauth.httpx.Request("POST", url)
        raise oauth.httpx.HTTPStatusError(
            "server error",
            request=request,
            response=oauth.httpx.Response(500, request=request),
        )


class _GoogleRequestErrorAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        raise oauth.httpx.ConnectError(
            "network unreachable",
            request=oauth.httpx.Request("POST", url),
        )


class _GoogleErrorAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        return _FakeResponse(
            {
                "error": "invalid_grant",
                "error_description": "   ",
            }
        )

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        raise AssertionError("Google user lookup should not run for OAuth errors")


class _GoogleInvalidUserPayloadAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        return _FakeResponse({"access_token": "google-token"})

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(["not-a-dict"])


class _GoogleBlankTokenAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        return _FakeResponse({"access_token": "   "})

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        raise AssertionError("Google user lookup should not run for a blank access token")


class _GoogleMalformedTokenAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        return _FakeResponse({"access_token": "google-token\r\nInjected: header"})

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        raise AssertionError("Google user lookup should not run for a malformed access token")


class _GoogleWhitespaceUserFieldsAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        return _FakeResponse({"access_token": "google-token"})

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(
            {
                "id": " user-1 ",
                "email": " user@example.com ",
                "name": "   ",
                "picture": "   ",
            }
        )


class _GoogleUnsafeAvatarAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        return _FakeResponse({"access_token": "google-token"})

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(
            {
                "id": "user-1",
                "email": "user@example.com",
                "name": "User",
                "picture": "https://example.com/avatar.png?access_token=secret",
            }
        )


class _OAuthClientShouldNotStart:
    def __init__(self, *args, **kwargs) -> None:
        raise AssertionError("OAuth HTTP client should not start for blank codes")


def test_oauth_github_url_rejects_invalid_intent() -> None:
    with pytest.raises(ValueError, match="Invalid GitHub OAuth intent"):
        oauth.oauth_github_url(intent="bogus")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_exchange_github_code_rejects_blank_code_before_provider_call(
    monkeypatch,
) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _OAuthClientShouldNotStart)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_github_code("   ")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "GitHub OAuth failed: missing authorization code"


@pytest.mark.asyncio
async def test_exchange_google_code_rejects_blank_code_before_provider_call(
    monkeypatch,
) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _OAuthClientShouldNotStart)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_google_code("   ")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Google OAuth failed: missing authorization code"


@pytest.mark.asyncio
async def test_exchange_github_code_rejects_control_character_code_before_provider_call(
    monkeypatch,
) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _OAuthClientShouldNotStart)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_github_code("code\r\nInjected: header")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "GitHub OAuth failed: missing authorization code"


@pytest.mark.asyncio
async def test_exchange_google_code_rejects_control_character_code_before_provider_call(
    monkeypatch,
) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _OAuthClientShouldNotStart)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_google_code("code\r\nInjected: header")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Google OAuth failed: missing authorization code"


@pytest.mark.asyncio
async def test_exchange_github_code_sets_http_timeout(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubAsyncClient)

    data = await oauth.exchange_github_code("code")

    assert _GitHubAsyncClient.last_timeout == oauth._OAUTH_HTTP_TIMEOUT
    assert _GitHubAsyncClient.last_post_data == {
        "client_id": oauth._oauth_setting_text(oauth.settings.github_client_id),
        "client_secret": oauth._oauth_setting_text(oauth.settings.github_client_secret),
        "code": "code",
    }
    assert data["access_token"] == "gh-token"
    assert data["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_exchange_github_code_passes_redirect_uri(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubAsyncClient)

    await oauth.exchange_github_code(
        "code",
        redirect_uri="http://198.211.100.37/api/proxy/auth/github/callback",
    )

    assert _GitHubAsyncClient.last_post_data == {
        "client_id": oauth._oauth_setting_text(oauth.settings.github_client_id),
        "client_secret": oauth._oauth_setting_text(oauth.settings.github_client_secret),
        "code": "code",
        "redirect_uri": "http://198.211.100.37/api/proxy/auth/github/callback",
    }


@pytest.mark.asyncio
async def test_exchange_github_code_trims_whitespace_client_credentials(monkeypatch) -> None:
    monkeypatch.setattr(oauth.settings, "github_client_id", " github-client ")
    monkeypatch.setattr(oauth.settings, "github_client_secret", " github-secret ")
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubAsyncClient)

    await oauth.exchange_github_code("code")

    assert _GitHubAsyncClient.last_post_data == {
        "client_id": "github-client",
        "client_secret": "github-secret",
        "code": "code",
    }


class _GitHubErrorAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        _GitHubAsyncClient.last_post_data = data
        return _FakeResponse(
            {
                "error": "redirect_uri_mismatch",
                "error_description": "The redirect_uri is not associated with this application.",
            }
        )


@pytest.mark.asyncio
async def test_exchange_github_code_maps_oauth_errors(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_github_code("code")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "GitHub OAuth failed: The redirect_uri is not associated with this application."
    )


class _GitHubBlankErrorDescriptionAsyncClient(_GitHubAsyncClient):
    async def post(self, url: str, data: dict, headers: dict | None = None) -> _FakeResponse:
        _GitHubAsyncClient.last_post_data = data
        return _FakeResponse(
            {
                "error": "redirect_uri_mismatch",
                "error_description": "   ",
            }
        )


@pytest.mark.asyncio
async def test_exchange_github_code_falls_back_to_error_code_for_blank_error_description(
    monkeypatch,
) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubBlankErrorDescriptionAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_github_code("code")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "GitHub OAuth failed: redirect_uri_mismatch"


@pytest.mark.asyncio
async def test_exchange_google_code_maps_timeout_to_gateway_timeout(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GoogleTimeoutAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_google_code("code")

    assert exc_info.value.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert exc_info.value.detail == "Google OAuth timed out after 20s"


@pytest.mark.asyncio
async def test_exchange_google_code_maps_oauth_errors(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GoogleErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_google_code("code")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Google OAuth failed: invalid_grant"


@pytest.mark.asyncio
async def test_exchange_github_code_maps_transport_failures_to_bad_gateway(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubRequestErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_github_code("code")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub OAuth failed. Try again."


@pytest.mark.asyncio
async def test_exchange_google_code_maps_transport_failures_to_bad_gateway(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GoogleRequestErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_google_code("code")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "Google OAuth failed. Try again."


@pytest.mark.asyncio
async def test_exchange_github_code_maps_upstream_status_errors_to_bad_gateway(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubServerErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_github_code("code")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub OAuth failed. Try again."


@pytest.mark.asyncio
async def test_exchange_github_code_rejects_invalid_user_payload(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubInvalidUserPayloadAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_github_code("code")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub OAuth failed. Try again."


@pytest.mark.asyncio
async def test_exchange_github_code_rejects_blank_access_token(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubBlankTokenAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_github_code("code")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub OAuth failed. Try again."


@pytest.mark.asyncio
async def test_exchange_github_code_rejects_malformed_access_token(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubMalformedTokenAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_github_code("code")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub OAuth failed. Try again."


@pytest.mark.asyncio
async def test_exchange_github_code_maps_invalid_json_to_bad_gateway(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubInvalidJsonAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_github_code("code")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub OAuth failed. Try again."


@pytest.mark.asyncio
async def test_exchange_github_code_normalizes_whitespace_profile_fields(
    monkeypatch,
) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubWhitespaceUserFieldsAsyncClient)

    data = await oauth.exchange_github_code("code")

    assert data == {
        "id": "123",
        "email": None,
        "name": "user",
        "avatar_url": None,
        "access_token": "gh-token",
    }


@pytest.mark.asyncio
async def test_exchange_github_code_drops_unsafe_avatar_url(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubUnsafeAvatarAsyncClient)

    data = await oauth.exchange_github_code("code")

    assert data["avatar_url"] is None


@pytest.mark.asyncio
async def test_exchange_github_code_trims_whitespace_user_id(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubWhitespaceUserIdAsyncClient)

    data = await oauth.exchange_github_code("code")

    assert data["id"] == "123"


@pytest.mark.asyncio
async def test_exchange_github_code_coerces_string_email_flags(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubStringEmailFlagsAsyncClient)

    data = await oauth.exchange_github_code("code")

    assert data["email"] == "right@example.com"


@pytest.mark.asyncio
async def test_exchange_google_code_maps_upstream_status_errors_to_bad_gateway(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GoogleServerErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_google_code("code")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "Google OAuth failed. Try again."


@pytest.mark.asyncio
async def test_exchange_google_code_rejects_invalid_user_payload(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GoogleInvalidUserPayloadAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_google_code("code")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "Google OAuth failed. Try again."


@pytest.mark.asyncio
async def test_exchange_google_code_rejects_blank_access_token(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GoogleBlankTokenAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_google_code("code")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "Google OAuth failed. Try again."


@pytest.mark.asyncio
async def test_exchange_google_code_rejects_malformed_access_token(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GoogleMalformedTokenAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await oauth.exchange_google_code("code")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "Google OAuth failed. Try again."


@pytest.mark.asyncio
async def test_exchange_google_code_normalizes_whitespace_profile_fields(
    monkeypatch,
) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GoogleWhitespaceUserFieldsAsyncClient)

    data = await oauth.exchange_google_code("code")

    assert data == {
        "id": "user-1",
        "email": "user@example.com",
        "name": None,
        "avatar_url": None,
        "access_token": "google-token",
    }


@pytest.mark.asyncio
async def test_exchange_google_code_drops_unsafe_avatar_url(monkeypatch) -> None:
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GoogleUnsafeAvatarAsyncClient)

    data = await oauth.exchange_google_code("code")

    assert data["avatar_url"] is None


@pytest.mark.asyncio
async def test_exchange_google_code_trims_whitespace_client_credentials(monkeypatch) -> None:
    monkeypatch.setattr(oauth.settings, "google_client_id", " google-client ")
    monkeypatch.setattr(oauth.settings, "google_client_secret", " google-secret ")
    monkeypatch.setattr(oauth.httpx, "AsyncClient", _GitHubAsyncClient)

    await oauth.exchange_google_code("code")

    assert _GitHubAsyncClient.last_post_data == {
        "client_id": "google-client",
        "client_secret": "google-secret",
        "code": "code",
        "grant_type": "authorization_code",
        "redirect_uri": oauth._build_callback_url("google"),
    }
