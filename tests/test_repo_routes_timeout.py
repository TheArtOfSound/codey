from __future__ import annotations

import pytest
from fastapi import HTTPException, status

import codey.saas.api.repo_routes as repo_routes


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {"full_name": "owner/repo"}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    last_timeout: float | None = None
    last_url: str | None = None
    last_headers: dict[str, str] | None = None

    def __init__(self, *args, timeout: float | None = None, **kwargs) -> None:
        _FakeAsyncClient.last_timeout = timeout

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        _FakeAsyncClient.last_url = url
        _FakeAsyncClient.last_headers = headers
        return _FakeResponse()


class _TimeoutAsyncClient(_FakeAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        raise repo_routes.httpx.TimeoutException("timed out")


class _ServerErrorResponse(_FakeResponse):
    def __init__(self, url: str) -> None:
        super().__init__(status_code=500, payload={})
        self._url = url

    def raise_for_status(self) -> None:
        request = repo_routes.httpx.Request("GET", self._url)
        raise repo_routes.httpx.HTTPStatusError(
            "server error",
            request=request,
            response=repo_routes.httpx.Response(500, request=request),
        )


class _ServerErrorAsyncClient(_FakeAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        return _ServerErrorResponse(url)


class _RequestErrorAsyncClient(_FakeAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        raise repo_routes.httpx.ConnectError(
            "network unreachable",
            request=repo_routes.httpx.Request("GET", url),
        )


class _NotFoundAsyncClient(_FakeAsyncClient):
    def __init__(self, *args, status_code: int = 404, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._status_code = status_code

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(status_code=self._status_code)


class _InvalidPayloadAsyncClient(_FakeAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(payload=["not-a-dict"])


class _InvalidJsonResponse(_FakeResponse):
    def json(self) -> dict:
        raise ValueError("invalid json")


class _InvalidJsonAsyncClient(_FakeAsyncClient):
    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        return _InvalidJsonResponse()


@pytest.mark.asyncio
async def test_fetch_github_repo_info_sets_http_timeout(monkeypatch) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _FakeAsyncClient)

    data = await repo_routes._fetch_github_repo_info("owner/repo", token=None)

    assert _FakeAsyncClient.last_timeout == repo_routes._GITHUB_API_TIMEOUT
    assert data["full_name"] == "owner/repo"


@pytest.mark.asyncio
async def test_fetch_github_repo_info_rejects_invalid_full_name_before_request(
    monkeypatch,
) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_url = None

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._fetch_github_repo_info(  # type: ignore[arg-type]
            {"full_name": "owner/repo"},
            token=None,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid GitHub repository name."
    assert _FakeAsyncClient.last_url is None


@pytest.mark.asyncio
async def test_fetch_github_repo_info_rejects_invalid_full_name_string_before_request(
    monkeypatch,
) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_url = None

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._fetch_github_repo_info(
            "owner/repo/extra",
            token=None,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid GitHub repository name."
    assert _FakeAsyncClient.last_url is None


@pytest.mark.asyncio
async def test_fetch_github_repo_info_ignores_malformed_token_header(monkeypatch) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_headers = None

    await repo_routes._fetch_github_repo_info(  # type: ignore[arg-type]
        "owner/repo",
        token={"token": "gh-token"},
    )

    assert _FakeAsyncClient.last_headers == {"Accept": "application/vnd.github+json"}


@pytest.mark.asyncio
async def test_fetch_github_repo_info_ignores_newline_token_headers(monkeypatch) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_headers = None

    await repo_routes._fetch_github_repo_info(
        "owner/repo",
        token="gh-token\nX-Injected: bad",
    )

    assert _FakeAsyncClient.last_headers == {"Accept": "application/vnd.github+json"}


@pytest.mark.asyncio
async def test_fetch_github_repo_info_ignores_ascii_control_token_headers(
    monkeypatch,
) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_headers = None

    await repo_routes._fetch_github_repo_info(
        "owner/repo",
        token="gh-token\tbad",
    )

    assert _FakeAsyncClient.last_headers == {"Accept": "application/vnd.github+json"}


@pytest.mark.asyncio
async def test_fetch_github_repo_info_maps_timeout_to_gateway_timeout(monkeypatch) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _TimeoutAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._fetch_github_repo_info("owner/repo", token=None)

    assert exc_info.value.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert exc_info.value.detail == "GitHub API timed out after 20s"


@pytest.mark.asyncio
async def test_fetch_github_repo_info_maps_transport_failures_to_bad_gateway(monkeypatch) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _RequestErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._fetch_github_repo_info("owner/repo", token=None)

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub API request failed. Try again."


@pytest.mark.asyncio
async def test_fetch_github_repo_info_maps_upstream_status_errors_to_bad_gateway(monkeypatch) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _ServerErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._fetch_github_repo_info("owner/repo", token=None)

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub API request failed. Try again."


@pytest.mark.asyncio
async def test_fetch_github_repo_info_private_repo_hint_without_token(monkeypatch) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _NotFoundAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._fetch_github_repo_info("owner/private-repo", token=None)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == (
        "GitHub repository 'owner/private-repo' not found. If this repo is private, "
        "connect GitHub from Settings before adding it."
    )


@pytest.mark.asyncio
async def test_fetch_github_repo_info_private_repo_hint_with_token(monkeypatch) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _NotFoundAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._fetch_github_repo_info("owner/private-repo", token="gh-token")

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == (
        "GitHub repository 'owner/private-repo' not found. If this repo is private or "
        "org-restricted, reconnect GitHub from Settings to refresh repository access."
    )


@pytest.mark.asyncio
async def test_fetch_github_repo_info_rejects_non_object_payload(monkeypatch) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _InvalidPayloadAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._fetch_github_repo_info("owner/repo", token=None)

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == (
        "GitHub API returned an invalid repository response. Try again."
    )


@pytest.mark.asyncio
async def test_fetch_github_repo_info_rejects_invalid_json_payload(monkeypatch) -> None:
    monkeypatch.setattr(repo_routes.httpx, "AsyncClient", _InvalidJsonAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._fetch_github_repo_info("owner/repo", token=None)

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == (
        "GitHub API returned an invalid repository response. Try again."
    )
