from __future__ import annotations

import uuid
import pytest
from fastapi import HTTPException, status
from types import SimpleNamespace

import codey.saas.api.github_routes as github_routes

_PR_FILE_OUTPUT = "## app.py\n```python\nprint('hello')\n```"


def test_github_headers_include_bearer_for_non_empty_string_token() -> None:
    headers = github_routes._github_headers("  gh-token  ")

    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["Authorization"] == "Bearer gh-token"


def test_github_headers_ignore_malformed_truthy_token() -> None:
    headers = github_routes._github_headers({"token": "gh-token"})  # type: ignore[arg-type]

    assert headers == {"Accept": "application/vnd.github+json"}


def test_github_headers_ignore_crlf_bearing_token() -> None:
    headers = github_routes._github_headers("gh-token\nInjected: header")

    assert headers == {"Accept": "application/vnd.github+json"}


def test_github_headers_ignore_ascii_control_token() -> None:
    headers = github_routes._github_headers("gh-token\tbad")

    assert headers == {"Accept": "application/vnd.github+json"}


def test_coerce_github_repo_full_name_rejects_malformed_values() -> None:
    assert github_routes._coerce_github_repo_full_name("owner/repo") == "owner/repo"
    assert (
        github_routes._coerce_github_repo_full_name(f"{'a' * 39}/repo")
        == f"{'a' * 39}/repo"
    )
    assert github_routes._coerce_github_repo_full_name(f"{'a' * 40}/repo") is None
    assert github_routes._coerce_github_repo_full_name("owner/repo\nbad") is None
    assert github_routes._coerce_github_repo_full_name("owner") is None
    assert github_routes._coerce_github_repo_full_name("owner/..") is None


def test_github_url_quote_helpers_encode_refs_and_file_paths() -> None:
    assert github_routes._quote_github_path_segment("feature/fix") == "feature%2Ffix"
    assert (
        github_routes._quote_github_file_path("src/my file.py")
        == "src/my%20file.py"
    )


def test_normalize_repo_file_path_rejects_nul_bytes() -> None:
    with pytest.raises(HTTPException) as exc_info:
        github_routes._normalize_repo_file_path("src/bad\x00name.py")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "Session output contains an invalid file path: src/bad\x00name.py"
    )


def test_normalize_repo_file_path_rejects_control_characters() -> None:
    with pytest.raises(HTTPException) as exc_info:
        github_routes._normalize_repo_file_path("src/bad\nname.py")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "Session output contains an invalid file path: src/bad\nname.py"
    )


class _TimeoutAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        return None

    async def __aenter__(self) -> _TimeoutAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, *args, **kwargs):
        raise github_routes.httpx.TimeoutException("timed out")


class _RequestErrorAsyncClient(_TimeoutAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        raise github_routes.httpx.ConnectError(
            "network unreachable",
            request=github_routes.httpx.Request("GET", url),
        )


class _ServerErrorResponse:
    def __init__(self, url: str) -> None:
        self.status_code = 500
        self._url = url

    def raise_for_status(self) -> None:
        request = github_routes.httpx.Request("GET", self._url)
        raise github_routes.httpx.HTTPStatusError(
            "server error",
            request=request,
            response=github_routes.httpx.Response(500, request=request),
        )

    def json(self):
        return []


class _ServerErrorAsyncClient(_TimeoutAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        return _ServerErrorResponse(url)


class _ForbiddenResponse:
    status_code = 403

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return []


class _ForbiddenAsyncClient(_TimeoutAsyncClient):
    async def get(self, *args, **kwargs):
        return _ForbiddenResponse()


class _InvalidJsonResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self):
        raise ValueError("invalid json")


class _InvalidJsonAsyncClient(_TimeoutAsyncClient):
    async def get(self, *args, **kwargs):
        return _InvalidJsonResponse()


class _InvalidIssuesPayloadResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {"number": 1}


class _InvalidIssuesPayloadAsyncClient(_TimeoutAsyncClient):
    async def get(self, *args, **kwargs):
        return _InvalidIssuesPayloadResponse()


class _InvalidIssueEntryResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return [123]


class _InvalidIssueEntryAsyncClient(_TimeoutAsyncClient):
    async def get(self, *args, **kwargs):
        return _InvalidIssueEntryResponse()


class _InvalidIssuePayloadResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return ["not-a-dict"]


class _InvalidIssuePayloadAsyncClient(_TimeoutAsyncClient):
    async def get(self, *args, **kwargs):
        return _InvalidIssuePayloadResponse()


class _ReviewInvalidFilesPayloadAsyncClient(_TimeoutAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        if url.endswith("/files"):
            return _ResponseStub(200, {"filename": "app.py"})
        if "/pulls/" in url:
            headers = kwargs.get("headers", {})
            if headers.get("Accept") == "application/vnd.github.diff":
                return _ResponseStub(200, text="diff --git a/app.py b/app.py")
            return _ResponseStub(200, {"title": "PR title", "body": "PR body"})
        raise AssertionError(f"Unexpected GET {url}")


class _ReviewMalformedPrDetailsAsyncClient(_TimeoutAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        if url.endswith("/files"):
            return _ResponseStub(200, [{"filename": "app.py"}])
        if "/pulls/" in url:
            headers = kwargs.get("headers", {})
            if headers.get("Accept") == "application/vnd.github.diff":
                return _ResponseStub(200, text="diff --git a/app.py b/app.py")
            return _ResponseStub(200, {"title": {"text": "PR title"}, "body": {"text": "PR body"}})
        raise AssertionError(f"Unexpected GET {url}")


class _ScalarResult:
    def __init__(self, obj) -> None:
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _CreatePrDB:
    def __init__(self, *results) -> None:
        self._results = list(results)

    async def execute(self, _statement):
        return _ScalarResult(self._results.pop(0))


class _RepoBoundCreatePrDB:
    def __init__(self, session, repo) -> None:
        self._session = session
        self._repo = repo
        self.statements: list[str] = []
        self._calls = 0

    async def execute(self, statement):
        text = str(statement)
        self.statements.append(text)
        self._calls += 1
        if self._calls == 1:
            return _ScalarResult(self._session)
        if "repositories.full_name" in text:
            return _ScalarResult(self._repo)
        return _ScalarResult(None)


class _MissingRepoBoundCreatePrDB:
    def __init__(self, session) -> None:
        self._session = session
        self.statements: list[str] = []
        self._calls = 0

    async def execute(self, statement):
        self.statements.append(str(statement))
        self._calls += 1
        if self._calls == 1:
            return _ScalarResult(self._session)
        return _ScalarResult(None)


class _FixIssueCaptureDB:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


class _ResponseStub:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _CreatePrUploadFailureAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        return None

    async def __aenter__(self) -> _CreatePrUploadFailureAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, *args, **kwargs):
        if "/git/ref/heads/" in url:
            return _ResponseStub(200, {"object": {"sha": "base-sha"}})
        if "/contents/" in url:
            return _ResponseStub(404, {})
        raise AssertionError(f"Unexpected GET {url}")

    async def post(self, url: str, *args, **kwargs):
        if url.endswith("/git/refs"):
            return _ResponseStub(201, {})
        raise AssertionError(f"Unexpected POST {url}")

    async def put(self, url: str, *args, **kwargs):
        if "/contents/" in url:
            return _ResponseStub(500, text="github write failed")
        raise AssertionError(f"Unexpected PUT {url}")


class _CreatePrBranchCreateFailureAsyncClient(_CreatePrUploadFailureAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        if "/git/ref/heads/" in url:
            return _ResponseStub(200, {"object": {"sha": "base-sha"}})
        raise AssertionError(f"Unexpected GET {url}")

    async def post(self, url: str, *args, **kwargs):
        if url.endswith("/git/refs"):
            return _ResponseStub(500, text="branch create failed")
        raise AssertionError(f"Unexpected POST {url}")


class _CreatePrFinalCreateFailureAsyncClient(_CreatePrUploadFailureAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        if "/git/ref/heads/" in url:
            return _ResponseStub(200, {"object": {"sha": "base-sha"}})
        raise AssertionError(f"Unexpected GET {url}")

    async def post(self, url: str, *args, **kwargs):
        if url.endswith("/git/refs"):
            return _ResponseStub(201, {})
        if url.endswith("/pulls"):
            return _ResponseStub(500, text="pull request create failed")
        raise AssertionError(f"Unexpected POST {url}")


class _CreatePrInvalidExistingFilePayloadAsyncClient(_CreatePrUploadFailureAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        if "/git/ref/heads/" in url:
            return _ResponseStub(200, {"object": {"sha": "base-sha"}})
        if "/contents/" in url:
            return _InvalidJsonResponse()
        raise AssertionError(f"Unexpected GET {url}")


class _CreatePrMissingExistingFileShaAsyncClient(_CreatePrUploadFailureAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        if "/git/ref/heads/" in url:
            return _ResponseStub(200, {"object": {"sha": "base-sha"}})
        if "/contents/" in url:
            return _ResponseStub(200, {})
        raise AssertionError(f"Unexpected GET {url}")


class _CreatePrExistingFileForbiddenAsyncClient(_CreatePrUploadFailureAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        if "/git/ref/heads/" in url:
            return _ResponseStub(200, {"object": {"sha": "base-sha"}})
        if "/contents/" in url:
            return _ResponseStub(403, {})
        raise AssertionError(f"Unexpected GET {url}")

    async def put(self, url: str, *args, **kwargs):
        raise AssertionError("file upload should not run after access denial")


class _CreatePrInvalidFinalPayloadAsyncClient(_CreatePrUploadFailureAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        if "/git/ref/heads/" in url:
            return _ResponseStub(200, {"object": {"sha": "base-sha"}})
        if "/contents/" in url:
            return _ResponseStub(404, {})
        raise AssertionError(f"Unexpected GET {url}")

    async def post(self, url: str, *args, **kwargs):
        if url.endswith("/git/refs"):
            return _ResponseStub(201, {})
        if url.endswith("/pulls"):
            return _InvalidJsonResponse()
        raise AssertionError(f"Unexpected POST {url}")


class _CreatePrMissingFinalFieldsAsyncClient(_CreatePrUploadFailureAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        if "/git/ref/heads/" in url:
            return _ResponseStub(200, {"object": {"sha": "base-sha"}})
        if "/contents/" in url:
            return _ResponseStub(404, {})
        raise AssertionError(f"Unexpected GET {url}")

    async def post(self, url: str, *args, **kwargs):
        if url.endswith("/git/refs"):
            return _ResponseStub(201, {})
        if url.endswith("/pulls"):
            return _ResponseStub(201, {})
        raise AssertionError(f"Unexpected POST {url}")


class _CreatePrBlankFinalFieldsAsyncClient(_CreatePrUploadFailureAsyncClient):
    async def get(self, url: str, *args, **kwargs):
        if "/git/ref/heads/" in url:
            return _ResponseStub(200, {"object": {"sha": "base-sha"}})
        if "/contents/" in url:
            return _ResponseStub(404, {})
        raise AssertionError(f"Unexpected GET {url}")

    async def post(self, url: str, *args, **kwargs):
        if url.endswith("/git/refs"):
            return _ResponseStub(201, {})
        if url.endswith("/pulls"):
            return _ResponseStub(
                201,
                {
                    "number": 123,
                    "html_url": "   ",
                    "title": "PR title",
                    "state": "open",
                },
            )
        raise AssertionError(f"Unexpected POST {url}")


class _CreatePrBoolNumberAsyncClient(_CreatePrBlankFinalFieldsAsyncClient):
    async def post(self, url: str, *args, **kwargs):
        if url.endswith("/git/refs"):
            return _ResponseStub(201, {})
        if url.endswith("/pulls"):
            return _ResponseStub(
                201,
                {
                    "number": True,
                    "html_url": "https://github.com/owner/repo/pull/1",
                    "title": "PR title",
                    "state": "open",
                },
            )
        raise AssertionError(f"Unexpected POST {url}")


class _UnexpectedAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        raise AssertionError("GitHub client should not be constructed")


class _CaptureTreeUrlAsyncClient:
    urls: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        return None

    async def __aenter__(self) -> "_CaptureTreeUrlAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, *args, **kwargs):
        self.urls.append(url)
        return _ResponseStub(
            200,
            {"tree": [{"type": "blob", "path": "src/app.py"}]},
        )


@pytest.mark.asyncio
async def test_fetch_repo_tree_returns_empty_list_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _TimeoutAsyncClient)

    result = await github_routes._fetch_repo_tree("owner/repo", "main", token=None)

    assert result == []


@pytest.mark.asyncio
async def test_fetch_repo_tree_maps_access_denials_to_forbidden(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ForbiddenAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes._fetch_repo_tree("owner/repo", "main", token="gh-token")

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "GitHub denied repository access. Reconnect GitHub and try again."


@pytest.mark.asyncio
async def test_fetch_repo_tree_returns_empty_list_on_invalid_json(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _InvalidJsonAsyncClient)

    result = await github_routes._fetch_repo_tree("owner/repo", "main", token=None)

    assert result == []


@pytest.mark.asyncio
async def test_fetch_repo_tree_returns_empty_list_on_non_dict_payload(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _InvalidIssuePayloadAsyncClient)

    result = await github_routes._fetch_repo_tree("owner/repo", "main", token=None)

    assert result == []


@pytest.mark.asyncio
async def test_fetch_repo_tree_returns_empty_list_for_non_string_repo_metadata(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    result = await github_routes._fetch_repo_tree(["owner/repo"], {"name": "main"}, token=None)

    assert result == []


@pytest.mark.asyncio
async def test_fetch_repo_tree_returns_empty_list_for_malformed_branch(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    result = await github_routes._fetch_repo_tree("owner/repo", "main\nbad", token=None)

    assert result == []


@pytest.mark.asyncio
async def test_fetch_repo_tree_url_encodes_branch_slashes(monkeypatch) -> None:
    _CaptureTreeUrlAsyncClient.urls = []
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _CaptureTreeUrlAsyncClient)

    result = await github_routes._fetch_repo_tree(
        "owner/repo",
        "feature/fix",
        token=None,
    )

    assert result == ["src/app.py"]
    assert _CaptureTreeUrlAsyncClient.urls == [
        "https://api.github.com/repos/owner/repo/git/trees/feature%2Ffix"
    ]


@pytest.mark.asyncio
async def test_fetch_file_content_returns_none_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _TimeoutAsyncClient)

    result = await github_routes._fetch_file_content(
        "owner/repo",
        "src/app.py",
        "main",
        token=None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_file_content_maps_access_denials_to_forbidden(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ForbiddenAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes._fetch_file_content(
            "owner/repo",
            "src/app.py",
            "main",
            token="gh-token",
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "GitHub denied repository access. Reconnect GitHub and try again."


@pytest.mark.asyncio
async def test_fetch_file_content_returns_none_on_invalid_json(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _InvalidJsonAsyncClient)

    result = await github_routes._fetch_file_content(
        "owner/repo",
        "src/app.py",
        "main",
        token=None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_file_content_returns_none_on_non_dict_payload(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _InvalidIssuePayloadAsyncClient)

    result = await github_routes._fetch_file_content(
        "owner/repo",
        "src/app.py",
        "main",
        token=None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_file_content_returns_none_for_non_string_repo_metadata(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    result = await github_routes._fetch_file_content(
        "owner/repo",
        ["src/app.py"],
        {"name": "main"},
        token=None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_file_content_returns_none_for_malformed_path_or_branch(
    monkeypatch,
) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    assert (
        await github_routes._fetch_file_content(
            "owner/repo",
            "src/bad\nname.py",
            "main",
            token=None,
        )
        is None
    )
    assert (
        await github_routes._fetch_file_content(
            "owner/repo",
            "src/app.py",
            "main\nbad",
            token=None,
        )
        is None
    )


@pytest.mark.asyncio
async def test_fetch_repo_tree_returns_empty_list_on_transport_failure(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _RequestErrorAsyncClient)

    result = await github_routes._fetch_repo_tree("owner/repo", "main", token=None)

    assert result == []


@pytest.mark.asyncio
async def test_fetch_file_content_returns_none_on_transport_failure(monkeypatch) -> None:
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _RequestErrorAsyncClient)

    result = await github_routes._fetch_file_content(
        "owner/repo",
        "src/app.py",
        "main",
        token=None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_identify_relevant_files_tolerates_malformed_issue_metadata() -> None:
    result = await github_routes._identify_relevant_files(
        {
            "title": 123,
            "body": None,
            "labels": [
                "bad-label",
                {"name": "API"},
                {"name": ""},
                {"name": "   "},
                {"name": None},
            ],
        },
        ["src/api/routes.py", None, 123, "tests/test_api.py"],
        "python",
    )

    assert result == ["src/api/routes.py"]


def test_issue_to_response_rejects_blank_required_strings() -> None:
    with pytest.raises(HTTPException) as exc_info:
        github_routes._issue_to_response(
            {
                "number": 1,
                "title": "   ",
                "state": "open",
                "created_at": "2025-01-01T00:00:00Z",
                "html_url": "https://github.com/owner/repo/issues/1",
            }
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub issues response was invalid. Try again."


def test_issue_to_response_rejects_non_positive_issue_numbers() -> None:
    with pytest.raises(HTTPException) as exc_info:
        github_routes._issue_to_response(
            {
                "number": 0,
                "title": "Bug",
                "state": "open",
                "created_at": "2025-01-01T00:00:00Z",
                "html_url": "https://github.com/owner/repo/issues/0",
            }
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub issues response was invalid. Try again."


def test_issue_to_response_normalizes_blank_optional_fields() -> None:
    response = github_routes._issue_to_response(
        {
            "number": 7,
            "title": "Bug",
            "state": "open",
            "created_at": "2025-01-01T00:00:00Z",
            "html_url": "https://github.com/owner/repo/issues/7",
            "labels": [{"name": "bug"}, {"name": "   "}],
            "assignee": {"login": "   "},
            "body": "   ",
        }
    )

    assert response is not None
    assert response.labels == ["bug"]
    assert response.assignee is None
    assert response.body is None


@pytest.mark.asyncio
async def test_identify_relevant_files_tolerates_non_string_language() -> None:
    result = await github_routes._identify_relevant_files(
        {"title": "api bug", "body": "", "labels": []},
        ["src/api/routes.py", "docs/guide.md"],
        ["python"],
    )

    assert result == ["src/api/routes.py"]


@pytest.mark.asyncio
async def test_identify_relevant_files_fallback_skips_non_string_tree_entries() -> None:
    result = await github_routes._identify_relevant_files(
        {"title": "", "body": "", "labels": []},
        ["src/app.py", None, 123, "README.md"],
        "python",
    )

    assert result == ["src/app.py"]


@pytest.mark.asyncio
async def test_list_issues_rejects_invalid_state_before_repo_lookup() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            state="draft",
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid issue state. Expected open, closed, or all."


@pytest.mark.asyncio
async def test_list_issues_rejects_malformed_state_before_repo_lookup() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            state=["open"],
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid issue state. Expected open, closed, or all."


@pytest.mark.asyncio
async def test_list_issues_rejects_non_positive_pagination_before_repo_lookup() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            per_page=0,
            page=0,
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "per_page and page must be positive integers"


@pytest.mark.asyncio
async def test_list_issues_rejects_bool_pagination_before_repo_lookup() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            per_page=True,
            page=1,
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "per_page and page must be positive integers"


@pytest.mark.asyncio
async def test_list_issues_maps_timeout_to_gateway_timeout(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _TimeoutAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert exc_info.value.detail == "GitHub issues request timed out. Try again."


@pytest.mark.asyncio
async def test_list_issues_maps_transport_failures_to_bad_gateway(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _RequestErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub issues request failed. Try again."


@pytest.mark.asyncio
async def test_list_issues_maps_upstream_status_errors_to_bad_gateway(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ServerErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub issues request failed. Try again."


@pytest.mark.asyncio
async def test_list_issues_maps_access_denials_to_forbidden(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ForbiddenAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            current_user=SimpleNamespace(github_token="gh-token"),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "GitHub denied repository access. Reconnect GitHub and try again."


@pytest.mark.asyncio
async def test_list_issues_allows_missing_github_token_attribute(monkeypatch) -> None:
    captured_headers: list[dict[str, str]] = []

    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    class _IssuesAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            captured_headers.append(kwargs.get("headers", {}))
            return _ResponseStub(
                200,
                [
                    {
                        "number": 7,
                        "title": "Bug",
                        "state": "open",
                        "created_at": "2025-01-01T00:00:00Z",
                        "html_url": "https://github.com/owner/repo/issues/7",
                    }
                ],
            )

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _IssuesAsyncClient)

    issues = await github_routes.list_issues(
        "repo-id",
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert len(issues) == 1
    assert issues[0].number == 7
    assert captured_headers == [{"Accept": "application/vnd.github+json"}]


@pytest.mark.asyncio
async def test_list_issues_rejects_invalid_json_payload(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _InvalidJsonAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub issues response was invalid. Try again."


@pytest.mark.asyncio
async def test_list_issues_rejects_non_list_payload(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _InvalidIssuesPayloadAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub issues response was invalid. Try again."


@pytest.mark.asyncio
async def test_list_issues_rejects_invalid_issue_entries(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _InvalidIssueEntryAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub issues response was invalid. Try again."


@pytest.mark.asyncio
async def test_list_issues_rejects_non_string_repo_full_name(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name=["owner/repo"])

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no GitHub full_name set"


@pytest.mark.asyncio
async def test_list_issues_rejects_missing_repo_full_name(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace()

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no GitHub full_name set"


@pytest.mark.asyncio
async def test_list_issues_rejects_malformed_repo_full_name(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo\nbad")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.list_issues(
            "repo-id",
            current_user=SimpleNamespace(github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no GitHub full_name set"


@pytest.mark.asyncio
async def test_fix_issue_maps_timeout_to_gateway_timeout(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _TimeoutAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.fix_issue(
            "repo-id",
            123,
            body=github_routes.FixIssueRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert exc_info.value.detail == "GitHub issue request timed out. Try again."


@pytest.mark.asyncio
async def test_fix_issue_rejects_non_positive_issue_numbers_before_repo_lookup() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await github_routes.fix_issue(
            "repo-id",
            0,
            body=github_routes.FixIssueRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "issue_number must be a positive integer"


@pytest.mark.asyncio
async def test_fix_issue_rejects_bool_issue_numbers_before_repo_lookup() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await github_routes.fix_issue(
            "repo-id",
            True,
            body=github_routes.FixIssueRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "issue_number must be a positive integer"


@pytest.mark.asyncio
async def test_fix_issue_rejects_missing_repo_full_name(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace()

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.fix_issue(
            "repo-id",
            123,
            body=github_routes.FixIssueRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no GitHub full_name set"


@pytest.mark.asyncio
async def test_fix_issue_maps_transport_failures_to_bad_gateway(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _RequestErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.fix_issue(
            "repo-id",
            123,
            body=github_routes.FixIssueRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub issue request failed. Try again."


@pytest.mark.asyncio
async def test_fix_issue_maps_upstream_status_errors_to_bad_gateway(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ServerErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.fix_issue(
            "repo-id",
            123,
            body=github_routes.FixIssueRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub issue request failed. Try again."


@pytest.mark.asyncio
async def test_fix_issue_maps_access_denials_to_forbidden(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ForbiddenAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.fix_issue(
            "repo-id",
            123,
            body=github_routes.FixIssueRequest(),
            current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "GitHub denied repository access. Reconnect GitHub and try again."


@pytest.mark.asyncio
async def test_fix_issue_rejects_invalid_json_payload(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _InvalidJsonAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.fix_issue(
            "repo-id",
            123,
            body=github_routes.FixIssueRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub issue response was invalid. Try again."


@pytest.mark.asyncio
async def test_fix_issue_rejects_non_dict_payload(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _InvalidIssuePayloadAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.fix_issue(
            "repo-id",
            123,
            body=github_routes.FixIssueRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub issue response was invalid. Try again."


@pytest.mark.asyncio
async def test_fix_issue_records_repo_on_created_session(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(
            full_name="owner/repo",
            language="python",
            default_branch="main",
        )

    class _IssueAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self) -> _IssueAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            return _ResponseStub(
                200,
                {
                    "title": "Bug",
                    "body": "Broken behavior",
                    "labels": [],
                },
            )

    class _SandboxStub:
        async def create(self, **kwargs):
            return SimpleNamespace(id="sandbox-1")

        async def write_file(self, *args, **kwargs) -> None:
            return None

        async def read_file(self, *args, **kwargs):
            raise FileNotFoundError

    async def fake_identify_relevant_files(issue_data, tree_files, language):
        return []

    async def fake_fetch_repo_tree(*args, **kwargs):
        return []

    async def fake_run(**kwargs):
        return SimpleNamespace(content="No file changes needed")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _IssueAsyncClient)
    monkeypatch.setattr(github_routes, "_fetch_repo_tree", fake_fetch_repo_tree)
    monkeypatch.setattr(github_routes, "_identify_relevant_files", fake_identify_relevant_files)
    monkeypatch.setattr(github_routes, "_sandbox_mgr", _SandboxStub())
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    db = _FixIssueCaptureDB()

    await github_routes.fix_issue(
        "repo-id",
        123,
        body=github_routes.FixIssueRequest(),
        current_user=SimpleNamespace(id="user-1", github_token=None),
        db=db,
    )

    assert db.added[0].repo_connected == "owner/repo"


@pytest.mark.asyncio
async def test_fix_issue_allows_missing_github_token_attribute(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(
            full_name="owner/repo",
            language="python",
            default_branch="main",
        )

    class _IssueAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            calls["issue_headers"] = kwargs.get("headers")
            return _ResponseStub(
                200,
                {
                    "title": "Bug",
                    "body": "Broken behavior",
                    "labels": [],
                },
            )

    class _SandboxStub:
        async def create(self, **kwargs):
            return SimpleNamespace(id="sandbox-1")

        async def write_file(self, *args, **kwargs) -> None:
            return None

        async def read_file(self, *args, **kwargs):
            raise FileNotFoundError

    async def fake_fetch_repo_tree(full_name, branch, token):
        calls["tree"] = (full_name, branch, token)
        return ["src/app.py"]

    async def fake_identify_relevant_files(issue_data, tree_files, language):
        return ["src/app.py"]

    async def fake_fetch_file_content(full_name, path, branch, token):
        calls["file_content"] = (full_name, path, branch, token)
        return "print('hello')"

    async def fake_run(**kwargs):
        return SimpleNamespace(content="No file changes needed")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _IssueAsyncClient)
    monkeypatch.setattr(github_routes, "_fetch_repo_tree", fake_fetch_repo_tree)
    monkeypatch.setattr(github_routes, "_identify_relevant_files", fake_identify_relevant_files)
    monkeypatch.setattr(github_routes, "_fetch_file_content", fake_fetch_file_content)
    monkeypatch.setattr(github_routes, "_sandbox_mgr", _SandboxStub())
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    response = await github_routes.fix_issue(
        "repo-id",
        123,
        body=github_routes.FixIssueRequest(),
        current_user=SimpleNamespace(id="user-1"),
        db=_FixIssueCaptureDB(),
    )

    assert response.status == "completed"
    assert calls["issue_headers"] == {"Accept": "application/vnd.github+json"}
    assert calls["tree"] == ("owner/repo", "main", None)
    assert calls["file_content"] == ("owner/repo", "src/app.py", "main", None)


@pytest.mark.asyncio
async def test_fix_issue_normalizes_malformed_repo_language_and_branch(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(
            full_name="owner/repo",
            language=["python"],
            default_branch={"name": "main"},
        )

    class _IssueAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self) -> _IssueAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            return _ResponseStub(
                200,
                {
                    "title": "Bug",
                    "body": "Broken behavior",
                    "labels": [],
                },
            )

    class _SandboxStub:
        async def create(self, **kwargs):
            return SimpleNamespace(id="sandbox-1")

        async def write_file(self, *args, **kwargs) -> None:
            return None

        async def read_file(self, *args, **kwargs):
            raise FileNotFoundError

    async def fake_identify_relevant_files(issue_data, tree_files, language):
        calls["language"] = language
        return ["src/app.py"]

    async def fake_fetch_repo_tree(full_name, branch, token):
        calls["tree"] = (full_name, branch)
        return ["src/app.py"]

    async def fake_fetch_file_content(full_name, path, branch, token):
        calls["file_content"] = (full_name, path, branch)
        return "print('hello')"

    async def fake_run(**kwargs):
        calls["context"] = kwargs["context"]
        return SimpleNamespace(content="No file changes needed")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _IssueAsyncClient)
    monkeypatch.setattr(github_routes, "_fetch_repo_tree", fake_fetch_repo_tree)
    monkeypatch.setattr(github_routes, "_identify_relevant_files", fake_identify_relevant_files)
    monkeypatch.setattr(github_routes, "_fetch_file_content", fake_fetch_file_content)
    monkeypatch.setattr(github_routes, "_sandbox_mgr", _SandboxStub())
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    response = await github_routes.fix_issue(
        "repo-id",
        123,
        body=github_routes.FixIssueRequest(),
        current_user=SimpleNamespace(id="user-1", github_token=None),
        db=_FixIssueCaptureDB(),
    )

    assert response.status == "completed"
    assert calls["tree"] == ("owner/repo", "main")
    assert calls["language"] is None
    assert calls["file_content"] == ("owner/repo", "src/app.py", "main")
    assert calls["context"]["language"] == "python"


@pytest.mark.asyncio
async def test_fix_issue_ignores_invalid_label_entries(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(
            full_name="owner/repo",
            language="python",
            default_branch="main",
        )

    class _IssueAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self) -> _IssueAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            return _ResponseStub(
                200,
                {
                    "title": "Bug",
                    "body": "Broken behavior",
                    "labels": [{"name": "bug"}, 123, {"name": None}],
                },
            )

    class _SandboxStub:
        async def create(self, **kwargs):
            return SimpleNamespace(id="sandbox-1")

        async def write_file(self, *args, **kwargs) -> None:
            return None

        async def read_file(self, *args, **kwargs):
            raise FileNotFoundError

    async def fake_identify_relevant_files(issue_data, tree_files, language):
        return []

    async def fake_fetch_repo_tree(*args, **kwargs):
        return []

    async def fake_run(**kwargs):
        return SimpleNamespace(content="No file changes needed")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _IssueAsyncClient)
    monkeypatch.setattr(github_routes, "_fetch_repo_tree", fake_fetch_repo_tree)
    monkeypatch.setattr(github_routes, "_identify_relevant_files", fake_identify_relevant_files)
    monkeypatch.setattr(github_routes, "_sandbox_mgr", _SandboxStub())
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    response = await github_routes.fix_issue(
        "repo-id",
        123,
        body=github_routes.FixIssueRequest(),
        current_user=SimpleNamespace(id="user-1", github_token=None),
        db=_FixIssueCaptureDB(),
    )

    assert response.status == "completed"
    assert response.files_modified == []


@pytest.mark.asyncio
async def test_fix_issue_normalizes_malformed_issue_title_and_body(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(
            full_name="owner/repo",
            language="python",
            default_branch="main",
        )

    class _IssueAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self) -> _IssueAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            return _ResponseStub(
                200,
                {
                    "title": {"text": "Bug"},
                    "body": {"text": "Broken behavior"},
                    "labels": [],
                },
            )

    class _SandboxStub:
        async def create(self, **kwargs):
            return SimpleNamespace(id="sandbox-1")

        async def write_file(self, *args, **kwargs) -> None:
            return None

        async def read_file(self, *args, **kwargs):
            raise FileNotFoundError

    async def fake_identify_relevant_files(issue_data, tree_files, language):
        return []

    async def fake_fetch_repo_tree(*args, **kwargs):
        return []

    async def fake_run(**kwargs):
        calls["request"] = kwargs["request"]
        return SimpleNamespace(content="No file changes needed.")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _IssueAsyncClient)
    monkeypatch.setattr(github_routes, "_fetch_repo_tree", fake_fetch_repo_tree)
    monkeypatch.setattr(github_routes, "_identify_relevant_files", fake_identify_relevant_files)
    monkeypatch.setattr(github_routes, "_sandbox_mgr", _SandboxStub())
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    db = _FixIssueCaptureDB()
    response = await github_routes.fix_issue(
        "repo-id",
        123,
        body=github_routes.FixIssueRequest(),
        current_user=SimpleNamespace(id="user-1", github_token=None),
        db=db,
    )

    assert response.status == "completed"
    assert "Fix GitHub issue #123: \n\nDescription:\n\n\nLabels:" in calls["request"]
    assert "{'text': 'Bug'}" not in calls["request"]
    assert "{'text': 'Broken behavior'}" not in calls["request"]


@pytest.mark.asyncio
async def test_fix_issue_rejects_invalid_generated_file_paths(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(
            full_name="owner/repo",
            language="python",
            default_branch="main",
        )

    class _IssueAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self) -> _IssueAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            return _ResponseStub(
                200,
                {
                    "title": "Bug",
                    "body": "Broken behavior",
                    "labels": [],
                },
            )

    class _SandboxStub:
        async def create(self, **kwargs):
            return SimpleNamespace(id="sandbox-1")

        async def write_file(self, *args, **kwargs) -> None:
            raise AssertionError("sandbox writes should not happen for invalid paths")

        async def read_file(self, *args, **kwargs):
            raise FileNotFoundError

    async def fake_identify_relevant_files(issue_data, tree_files, language):
        return []

    async def fake_fetch_repo_tree(*args, **kwargs):
        return []

    async def fake_run(**kwargs):
        return SimpleNamespace(
            content="## ../secrets.py\n```python\nprint('oops')\n```"
        )

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _IssueAsyncClient)
    monkeypatch.setattr(github_routes, "_fetch_repo_tree", fake_fetch_repo_tree)
    monkeypatch.setattr(github_routes, "_identify_relevant_files", fake_identify_relevant_files)
    monkeypatch.setattr(github_routes, "_sandbox_mgr", _SandboxStub())
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.fix_issue(
            "repo-id",
            123,
            body=github_routes.FixIssueRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "Session output contains an invalid file path: ../secrets.py"
    )


@pytest.mark.asyncio
async def test_fix_issue_fails_closed_for_non_string_model_output(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(
            full_name="owner/repo",
            language="python",
            default_branch="main",
        )

    class _IssueAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self) -> _IssueAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            return _ResponseStub(
                200,
                {
                    "title": "Bug",
                    "body": "Broken behavior",
                    "labels": [],
                },
            )

    class _SandboxStub:
        async def create(self, **kwargs):
            return SimpleNamespace(id="sandbox-1")

        async def write_file(self, *args, **kwargs) -> None:
            raise AssertionError("sandbox writes should not happen for non-string output")

        async def read_file(self, *args, **kwargs):
            raise FileNotFoundError

    async def fake_identify_relevant_files(issue_data, tree_files, language):
        return []

    async def fake_fetch_repo_tree(*args, **kwargs):
        return []

    async def fake_run(**kwargs):
        return SimpleNamespace(content={"plan": "not a string"})

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _IssueAsyncClient)
    monkeypatch.setattr(github_routes, "_fetch_repo_tree", fake_fetch_repo_tree)
    monkeypatch.setattr(github_routes, "_identify_relevant_files", fake_identify_relevant_files)
    monkeypatch.setattr(github_routes, "_sandbox_mgr", _SandboxStub())
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    db = _FixIssueCaptureDB()
    response = await github_routes.fix_issue(
        "repo-id",
        123,
        body=github_routes.FixIssueRequest(),
        current_user=SimpleNamespace(id="user-1", github_token=None),
        db=db,
    )

    assert response.status == "completed"
    assert response.plan == ""
    assert response.files_modified == []
    assert db.added[0].output_summary == ""


@pytest.mark.asyncio
async def test_fix_issue_fails_closed_for_missing_model_output(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(
            full_name="owner/repo",
            language="python",
            default_branch="main",
        )

    class _IssueAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self) -> _IssueAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            return _ResponseStub(
                200,
                {
                    "title": "Bug",
                    "body": "Broken behavior",
                    "labels": [],
                },
            )

    class _SandboxStub:
        async def create(self, **kwargs):
            return SimpleNamespace(id="sandbox-1")

        async def write_file(self, *args, **kwargs) -> None:
            raise AssertionError("sandbox writes should not happen for missing output")

        async def read_file(self, *args, **kwargs):
            raise FileNotFoundError

    async def fake_identify_relevant_files(issue_data, tree_files, language):
        return []

    async def fake_fetch_repo_tree(*args, **kwargs):
        return []

    async def fake_run(**kwargs):
        return SimpleNamespace()

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _IssueAsyncClient)
    monkeypatch.setattr(github_routes, "_fetch_repo_tree", fake_fetch_repo_tree)
    monkeypatch.setattr(github_routes, "_identify_relevant_files", fake_identify_relevant_files)
    monkeypatch.setattr(github_routes, "_sandbox_mgr", _SandboxStub())
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    db = _FixIssueCaptureDB()
    response = await github_routes.fix_issue(
        "repo-id",
        123,
        body=github_routes.FixIssueRequest(),
        current_user=SimpleNamespace(id="user-1", github_token=None),
        db=db,
    )

    assert response.status == "completed"
    assert response.plan == ""
    assert response.files_modified == []
    assert db.added[0].output_summary == ""


@pytest.mark.asyncio
async def test_review_pr_maps_timeout_to_gateway_timeout(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _TimeoutAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.review_pr(
            "repo-id",
            123,
            body=github_routes.ReviewRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert exc_info.value.detail == "GitHub PR request timed out. Try again."


@pytest.mark.asyncio
async def test_review_pr_rejects_non_positive_pr_numbers_before_repo_lookup() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await github_routes.review_pr(
            "repo-id",
            0,
            body=github_routes.ReviewRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "pr_number must be a positive integer"


@pytest.mark.asyncio
async def test_review_pr_rejects_bool_pr_numbers_before_repo_lookup() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await github_routes.review_pr(
            "repo-id",
            True,
            body=github_routes.ReviewRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "pr_number must be a positive integer"


@pytest.mark.asyncio
async def test_review_pr_maps_access_denials_to_forbidden(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ForbiddenAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.review_pr(
            "repo-id",
            123,
            body=github_routes.ReviewRequest(),
            current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "GitHub denied repository access. Reconnect GitHub and try again."


@pytest.mark.asyncio
async def test_review_pr_maps_transport_failures_to_bad_gateway(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _RequestErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.review_pr(
            "repo-id",
            123,
            body=github_routes.ReviewRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR request failed. Try again."


@pytest.mark.asyncio
async def test_review_pr_maps_upstream_status_errors_to_bad_gateway(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ServerErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.review_pr(
            "repo-id",
            123,
            body=github_routes.ReviewRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR request failed. Try again."


@pytest.mark.asyncio
async def test_review_pr_rejects_invalid_pr_payload(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _InvalidJsonAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.review_pr(
            "repo-id",
            123,
            body=github_routes.ReviewRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR response was invalid. Try again."


@pytest.mark.asyncio
async def test_review_pr_rejects_non_list_changed_files_payload(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo")

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _ReviewInvalidFilesPayloadAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.review_pr(
            "repo-id",
            123,
            body=github_routes.ReviewRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR response was invalid. Try again."


@pytest.mark.asyncio
async def test_review_pr_rejects_non_string_repo_full_name(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name=["owner/repo"])

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.review_pr(
            "repo-id",
            123,
            body=github_routes.ReviewRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no GitHub full_name set"


@pytest.mark.asyncio
async def test_review_pr_rejects_missing_repo_full_name(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace()

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.review_pr(
            "repo-id",
            123,
            body=github_routes.ReviewRequest(),
            current_user=SimpleNamespace(id="user-1", github_token=None),
            db=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no GitHub full_name set"


@pytest.mark.asyncio
async def test_review_pr_normalizes_malformed_repo_language(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo", language=["python"])

    class _ReviewAsyncClient(_TimeoutAsyncClient):
        async def get(self, url: str, *args, **kwargs):
            if url.endswith("/files"):
                return _ResponseStub(200, [{"filename": "app.py"}])
            if "/pulls/" in url:
                headers = kwargs.get("headers", {})
                if headers.get("Accept") == "application/vnd.github.diff":
                    return _ResponseStub(200, text="diff --git a/app.py b/app.py")
                return _ResponseStub(200, {"title": "PR title", "body": "PR body"})
            raise AssertionError(f"Unexpected GET {url}")

    async def fake_run(**kwargs):
        calls["context"] = kwargs["context"]
        return SimpleNamespace(
            content='{"summary":"Looks good","score":0.9,"comments":[],"approved":true}'
        )

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ReviewAsyncClient)
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    response = await github_routes.review_pr(
        "repo-id",
        123,
        body=github_routes.ReviewRequest(),
        current_user=SimpleNamespace(id="user-1", github_token=None),
        db=object(),
    )

    assert response.approved is True
    assert calls["context"]["language"] == "python"


@pytest.mark.asyncio
async def test_review_pr_allows_missing_github_token_attribute(monkeypatch) -> None:
    captured_headers: list[dict[str, str]] = []

    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo", language="python")

    class _ReviewAsyncClient(_TimeoutAsyncClient):
        async def get(self, url: str, *args, **kwargs):
            captured_headers.append(kwargs.get("headers", {}))
            if url.endswith("/files"):
                return _ResponseStub(200, [{"filename": "app.py"}])
            if "/pulls/" in url:
                headers = kwargs.get("headers", {})
                if headers.get("Accept") == "application/vnd.github.diff":
                    return _ResponseStub(200, text="diff --git a/app.py b/app.py")
                return _ResponseStub(200, {"title": "PR title", "body": "PR body"})
            raise AssertionError(f"Unexpected GET {url}")

    async def fake_run(**kwargs):
        return SimpleNamespace(
            content='{"summary":"Looks good","score":0.9,"comments":[],"approved":true}'
        )

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ReviewAsyncClient)
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    response = await github_routes.review_pr(
        "repo-id",
        123,
        body=github_routes.ReviewRequest(),
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert response.approved is True
    assert captured_headers == [
        {"Accept": "application/vnd.github+json"},
        {"Accept": "application/vnd.github.diff"},
        {"Accept": "application/vnd.github+json"},
    ]


@pytest.mark.asyncio
async def test_review_pr_fails_closed_for_non_string_model_output(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo", language="python")

    class _ReviewAsyncClient(_TimeoutAsyncClient):
        async def get(self, url: str, *args, **kwargs):
            if url.endswith("/files"):
                return _ResponseStub(200, [{"filename": "app.py"}])
            if "/pulls/" in url:
                headers = kwargs.get("headers", {})
                if headers.get("Accept") == "application/vnd.github.diff":
                    return _ResponseStub(200, text="diff --git a/app.py b/app.py")
                return _ResponseStub(200, {"title": "PR title", "body": "PR body"})
            raise AssertionError(f"Unexpected GET {url}")

    async def fake_run(**kwargs):
        return SimpleNamespace(content={"summary": "Looks good"})

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ReviewAsyncClient)
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    response = await github_routes.review_pr(
        "repo-id",
        123,
        body=github_routes.ReviewRequest(),
        current_user=SimpleNamespace(id="user-1", github_token=None),
        db=object(),
    )

    assert response.summary == ""
    assert response.score == 0.5
    assert response.comments == []
    assert response.approved is False


@pytest.mark.asyncio
async def test_review_pr_fails_closed_for_missing_model_output(monkeypatch) -> None:
    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo", language="python")

    class _ReviewAsyncClient(_TimeoutAsyncClient):
        async def get(self, url: str, *args, **kwargs):
            if url.endswith("/files"):
                return _ResponseStub(200, [{"filename": "app.py"}])
            if "/pulls/" in url:
                headers = kwargs.get("headers", {})
                if headers.get("Accept") == "application/vnd.github.diff":
                    return _ResponseStub(200, text="diff --git a/app.py b/app.py")
                return _ResponseStub(200, {"title": "PR title", "body": "PR body"})
            raise AssertionError(f"Unexpected GET {url}")

    async def fake_run(**kwargs):
        return SimpleNamespace()

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ReviewAsyncClient)
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    response = await github_routes.review_pr(
        "repo-id",
        123,
        body=github_routes.ReviewRequest(),
        current_user=SimpleNamespace(id="user-1", github_token=None),
        db=object(),
    )

    assert response.summary == ""
    assert response.score == 0.5
    assert response.comments == []
    assert response.approved is False


@pytest.mark.asyncio
async def test_review_pr_normalizes_malformed_pr_title_and_body(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_get_repo(repo_id, user, db):
        return SimpleNamespace(full_name="owner/repo", language="python")

    async def fake_run(**kwargs):
        calls["request"] = kwargs["request"]
        return SimpleNamespace(
            content='{"summary":"Looks good","score":0.9,"comments":[],"approved":true}'
        )

    monkeypatch.setattr(github_routes, "_get_repo", fake_get_repo)
    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _ReviewMalformedPrDetailsAsyncClient,
    )
    monkeypatch.setattr(github_routes._intelligence, "run", fake_run)

    response = await github_routes.review_pr(
        "repo-id",
        123,
        body=github_routes.ReviewRequest(),
        current_user=SimpleNamespace(id="user-1", github_token=None),
        db=object(),
    )

    assert response.approved is True
    assert "PR #123: \n" in calls["request"]
    assert "Description: \n" in calls["request"]
    assert "{'text': 'PR title'}" not in calls["request"]
    assert "{'text': 'PR body'}" not in calls["request"]


@pytest.mark.asyncio
async def test_create_pull_request_rejects_sessions_without_file_changes(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary="Operator notes only.", repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )

    class _UnexpectedAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("GitHub should not be contacted for empty PR output")

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "Session output does not contain any file changes to open as a PR"
    )


@pytest.mark.asyncio
async def test_create_pull_request_rejects_invalid_output_file_paths(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(
            id=session_id,
            output_summary="## ../secrets.py\n```python\nprint('oops')\n```",
            repo_connected=None,
        ),
        SimpleNamespace(full_name="owner/repo"),
    )

    class _UnexpectedAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("GitHub should not be contacted for invalid PR paths")

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == (
        "Session output contains an invalid file path: ../secrets.py"
    )


@pytest.mark.asyncio
async def test_create_pull_request_maps_timeout_to_gateway_timeout(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _TimeoutAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert exc_info.value.detail == "GitHub PR creation timed out. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_rejects_malformed_github_token_before_network(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=SimpleNamespace(id="user-1", github_token={"token": "gh-token"}),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "GitHub authentication required to create PRs"


@pytest.mark.asyncio
async def test_create_pull_request_rejects_crlf_github_token_before_network(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=SimpleNamespace(
                id="user-1",
                github_token="gh-token\nInjected: header",
            ),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "GitHub authentication required to create PRs"


@pytest.mark.asyncio
async def test_create_pull_request_rejects_ascii_control_github_token_before_network(
    monkeypatch,
) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=SimpleNamespace(
                id="user-1",
                github_token="gh-token\tbad",
            ),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "GitHub authentication required to create PRs"


@pytest.mark.asyncio
async def test_create_pull_request_rejects_internal_whitespace_github_token_before_network(
    monkeypatch,
) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=SimpleNamespace(
                id="user-1",
                github_token="gh-token bad",
            ),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "GitHub authentication required to create PRs"


@pytest.mark.asyncio
async def test_create_pull_request_rejects_missing_github_token_attribute_before_network(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=SimpleNamespace(id="user-1"),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "GitHub authentication required to create PRs"


@pytest.mark.asyncio
async def test_create_pull_request_maps_transport_failures_to_bad_gateway(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _RequestErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_maps_upstream_status_errors_to_bad_gateway(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _ServerErrorAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_rejects_invalid_branch_ref_payload(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _InvalidJsonAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_rejects_missing_branch_sha(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _InvalidIssuesPayloadAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_fails_closed_when_file_upload_fails(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(
            id=session_id,
            output_summary="## app.py\n```python\nprint('hello')\n```",
            repo_connected=None,
        ),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _CreatePrUploadFailureAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub file upload failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_rejects_invalid_existing_file_payload(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(
            id=session_id,
            output_summary="## app.py\n```python\nprint('hello')\n```",
            repo_connected=None,
        ),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _CreatePrInvalidExistingFilePayloadAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_rejects_missing_existing_file_sha(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(
            id=session_id,
            output_summary="## app.py\n```python\nprint('hello')\n```",
            repo_connected=None,
        ),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _CreatePrMissingExistingFileShaAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_stops_when_existing_file_lookup_is_forbidden(
    monkeypatch,
) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(
            id=session_id,
            output_summary="## app.py\n```python\nprint('hello')\n```",
            repo_connected=None,
        ),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _CreatePrExistingFileForbiddenAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == (
        "GitHub denied repository access. Reconnect GitHub and try again."
    )


@pytest.mark.asyncio
async def test_create_pull_request_sanitizes_branch_creation_failures(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _CreatePrBranchCreateFailureAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_sanitizes_final_creation_failures(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _CreatePrFinalCreateFailureAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_rejects_invalid_final_payload(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _CreatePrInvalidFinalPayloadAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_rejects_missing_final_pr_fields(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _CreatePrMissingFinalFieldsAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_rejects_blank_final_pr_fields(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _CreatePrBlankFinalFieldsAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_rejects_bool_final_pr_number(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(
        github_routes.httpx,
        "AsyncClient",
        _CreatePrBoolNumberAsyncClient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub PR creation failed. Try again."


@pytest.mark.asyncio
async def test_create_pull_request_rejects_non_string_repo_full_name(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected=None),
        SimpleNamespace(full_name=["owner/repo"]),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "No connected repository found"


@pytest.mark.asyncio
async def test_create_pull_request_prefers_repo_connected_on_session(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _RepoBoundCreatePrDB(
        SimpleNamespace(id=session_id, output_summary=_PR_FILE_OUTPUT, repo_connected="owner/repo"),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _TimeoutAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert "repositories.full_name" in db.statements[1]


@pytest.mark.asyncio
async def test_create_pull_request_fails_closed_when_session_repo_is_disconnected() -> None:
    session_id = uuid.uuid4()
    db = _MissingRepoBoundCreatePrDB(
        SimpleNamespace(id=session_id, output_summary="", repo_connected="owner/repo")
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "The repository for this session is no longer connected"
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_create_pull_request_fails_closed_for_non_string_output_summary(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, output_summary={"files": ["app.py"]}, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Session output does not contain any file changes to open as a PR"


@pytest.mark.asyncio
async def test_create_pull_request_fails_closed_for_missing_output_summary(monkeypatch) -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(id=session_id, repo_connected=None),
        SimpleNamespace(full_name="owner/repo"),
    )
    current_user = SimpleNamespace(id="user-1", github_token="gh-token")

    monkeypatch.setattr(github_routes.httpx, "AsyncClient", _UnexpectedAsyncClient)

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=current_user,
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Session output does not contain any file changes to open as a PR"


@pytest.mark.asyncio
async def test_create_pull_request_falls_back_when_session_repo_connected_is_malformed() -> None:
    session_id = uuid.uuid4()
    db = _CreatePrDB(
        SimpleNamespace(
            id=session_id,
            output_summary="",
            repo_connected={"repo": "owner/repo"},
        ),
        SimpleNamespace(full_name="owner/repo"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await github_routes.create_pull_request(
            github_routes.CreatePRRequest(session_id=str(session_id), title="PR title"),
            current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Session output does not contain any file changes to open as a PR"
