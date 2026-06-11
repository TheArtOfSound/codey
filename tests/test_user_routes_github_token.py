from __future__ import annotations

import pytest
from fastapi import HTTPException, status

import codey.saas.api.user_routes as user_routes


class _FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _SuccessAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        return None

    async def __aenter__(self) -> _SuccessAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == user_routes._GITHUB_USER_URL:
            return _FakeResponse(
                200,
                {
                    "id": 123,
                    "email": None,
                    "name": "Repo User",
                    "login": "repo-user",
                    "avatar_url": "https://avatars.example/repo-user.png",
                },
            )
        return _FakeResponse(
            200,
            [
                {"email": "repo-user@example.com", "primary": True, "verified": True},
            ],
        )


class _RejectedAsyncClient(_SuccessAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(401, {})


class _ServerErrorResponse(_FakeResponse):
    def __init__(self, url: str) -> None:
        super().__init__(500, {})
        self._url = url

    def raise_for_status(self) -> None:
        raise user_routes.httpx.HTTPStatusError(
            "server error",
            request=user_routes.httpx.Request("GET", self._url),
            response=user_routes.httpx.Response(500, request=user_routes.httpx.Request("GET", self._url)),
        )


class _ServerErrorAsyncClient(_SuccessAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        return _ServerErrorResponse(url)


class _OfflineAsyncClient(_SuccessAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        raise user_routes.httpx.ConnectError(
            "network unreachable",
            request=user_routes.httpx.Request("GET", url),
        )


class _InvalidUserPayloadAsyncClient(_SuccessAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == user_routes._GITHUB_USER_URL:
            return _FakeResponse(200, ["not-a-dict"])
        return _FakeResponse(200, [])


class _WhitespaceUserPayloadAsyncClient(_SuccessAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == user_routes._GITHUB_USER_URL:
            return _FakeResponse(
                200,
                {
                    "id": 123,
                    "email": "   ",
                    "name": "   ",
                    "login": "repo-user",
                    "avatar_url": "   ",
                },
            )
        return _FakeResponse(
            200,
            [
                {"email": "   ", "primary": True, "verified": True},
            ],
        )


class _UnsafeAvatarUserPayloadAsyncClient(_SuccessAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == user_routes._GITHUB_USER_URL:
            return _FakeResponse(
                200,
                {
                    "id": 123,
                    "email": None,
                    "name": "Repo User",
                    "login": "repo-user",
                    "avatar_url": "https://avatars.example/repo-user.png?access_token=secret",
                },
            )
        return _FakeResponse(
            200,
            [
                {"email": "repo-user@example.com", "primary": True, "verified": True},
            ],
        )


class _WhitespaceUserIdPayloadAsyncClient(_SuccessAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == user_routes._GITHUB_USER_URL:
            return _FakeResponse(
                200,
                {
                    "id": " 123 ",
                    "email": "repo-user@example.com",
                    "name": "Repo User",
                    "login": "repo-user",
                    "avatar_url": None,
                },
            )
        return _FakeResponse([])


class _StringEmailFlagsAsyncClient(_SuccessAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        if url == user_routes._GITHUB_USER_URL:
            return _FakeResponse(
                200,
                {
                    "id": 123,
                    "email": None,
                    "name": "Repo User",
                    "login": "repo-user",
                    "avatar_url": "https://avatars.example/repo-user.png",
                },
            )
        return _FakeResponse(
            200,
            [
                {"email": "wrong@example.com", "primary": "false", "verified": "true"},
                {"email": "right@example.com", "primary": "true", "verified": "true"},
            ],
        )


class _UnexpectedAsyncClient(_SuccessAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        raise AssertionError("GitHub validation should not run for malformed tokens")


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_uses_primary_email(monkeypatch) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _SuccessAsyncClient)

    data = await user_routes._fetch_github_user_from_token("ghp_test_token")

    assert data == {
        "id": "123",
        "name": "Repo User",
        "avatar_url": "https://avatars.example/repo-user.png",
        "email": "repo-user@example.com",
    }


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_drops_unsafe_avatar_url(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        user_routes.httpx,
        "AsyncClient",
        _UnsafeAvatarUserPayloadAsyncClient,
    )

    data = await user_routes._fetch_github_user_from_token("ghp_test_token")

    assert data["avatar_url"] is None


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _RejectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await user_routes._fetch_github_user_from_token("bad-token")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "GitHub token was rejected. Use a token with repo, read:user, and user:email access."
    )


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_rejects_blank_token_before_request(
    monkeypatch,
) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await user_routes._fetch_github_user_from_token("   ")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "GitHub token was rejected. Use a token with repo, read:user, and user:email access."
    )


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_rejects_malformed_token_type_before_request(
    monkeypatch,
) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await user_routes._fetch_github_user_from_token({"token": "ghp_test_token"})  # type: ignore[arg-type]

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "GitHub token was rejected. Use a token with repo, read:user, and user:email access."
    )


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_rejects_line_break_token_before_request(
    monkeypatch,
) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await user_routes._fetch_github_user_from_token(
            "ghp_1234567890\nInjected: header"
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "GitHub token was rejected. Use a token with repo, read:user, and user:email access."
    )


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_rejects_ascii_control_token_before_request(
    monkeypatch,
) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await user_routes._fetch_github_user_from_token(
            "ghp_1234567890\tInjected"
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "GitHub token was rejected. Use a token with repo, read:user, and user:email access."
    )


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_rejects_internal_whitespace_token_before_request(
    monkeypatch,
) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await user_routes._fetch_github_user_from_token("ghp_1234567890 bad")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "GitHub token was rejected. Use a token with repo, read:user, and user:email access."
    )


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_handles_transport_failures(monkeypatch) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _OfflineAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await user_routes._fetch_github_user_from_token("ghp_test_token")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub validation failed. Try again."


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_handles_upstream_status_errors(monkeypatch) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _ServerErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await user_routes._fetch_github_user_from_token("ghp_test_token")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub validation failed. Try again."


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_rejects_invalid_user_payload(monkeypatch) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _InvalidUserPayloadAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await user_routes._fetch_github_user_from_token("ghp_test_token")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub validation failed. Try again."


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_normalizes_whitespace_profile_fields(
    monkeypatch,
) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _WhitespaceUserPayloadAsyncClient)

    data = await user_routes._fetch_github_user_from_token("ghp_test_token")

    assert data == {
        "id": "123",
        "name": "repo-user",
        "avatar_url": None,
        "email": None,
    }


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_trims_whitespace_user_id(monkeypatch) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _WhitespaceUserIdPayloadAsyncClient)

    data = await user_routes._fetch_github_user_from_token("ghp_test_token")

    assert data["id"] == "123"


@pytest.mark.asyncio
async def test_fetch_github_user_from_token_coerces_string_email_flags(monkeypatch) -> None:
    monkeypatch.setattr(user_routes.httpx, "AsyncClient", _StringEmailFlagsAsyncClient)

    data = await user_routes._fetch_github_user_from_token("ghp_test_token")

    assert data["email"] == "right@example.com"
