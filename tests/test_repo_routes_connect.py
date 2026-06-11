from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, status
from pydantic import ValidationError

import codey.saas.api.repo_routes as repo_routes


class _ScalarResult:
    def __init__(self, obj) -> None:
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _DuplicateAfterFetchDB:
    def __init__(self, state: dict[str, object]) -> None:
        self._state = state
        self.added: list[object] = []

    async def execute(self, statement):
        self._state["statement"] = str(statement)
        existing = SimpleNamespace(id="existing-repo") if self._state["fetched"] else None
        return _ScalarResult(existing)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


class _NoDuplicateDB:
    def __init__(self) -> None:
        self.added: list[object] = []

    async def execute(self, statement):
        return _ScalarResult(None)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "autonomous_mode_enabled", None) is None:
                obj.autonomous_mode_enabled = False
        return None


class _RepoLimitDB:
    def __init__(self, repo_count: int) -> None:
        self.repo_count = repo_count
        self.execute_calls = 0
        self.added: list[object] = []

    async def execute(self, statement):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _ScalarResult(None)
        return _ScalarResult(self.repo_count)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


def test_connect_repo_request_rejects_blank_url() -> None:
    with pytest.raises(ValidationError):
        repo_routes.ConnectRepoRequest(github_repo_url="   ")


def test_connect_repo_request_strips_url_text() -> None:
    request = repo_routes.ConnectRepoRequest(
        github_repo_url="  https://github.com/openai/openai-python  "
    )

    assert request.github_repo_url == "https://github.com/openai/openai-python"


def test_redact_repo_error_hides_common_secret_shapes() -> None:
    message = repo_routes._redact_repo_error(
        RuntimeError(
            "analysis failed https://user:url-secret@example.test/repo.git"
            "?access_token=query-secret authorization=Bearer bearer-secret "
            "mirror=https://example.test/repo.git#client_secret=fragment-secret "
            "for operator@example.test"
        )
    )

    assert "url-secret" not in message
    assert "query-secret" not in message
    assert "fragment-secret" not in message
    assert "bearer-secret" not in message
    assert "operator@example.test" not in message
    assert "https://***@example.test/repo.git" in message
    assert "access_token=***" in message
    assert "client_secret=***" in message
    assert "authorization=Bearer ***" in message
    assert "[redacted-email]" in message


@pytest.mark.asyncio
async def test_connect_repo_dedupes_case_insensitively_after_canonical_lookup(
    monkeypatch,
) -> None:
    state: dict[str, object] = {"fetched": False, "statement": ""}

    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        state["fetched"] = True
        return {
            "id": 123,
            "full_name": "openai/openai-python",
            "clone_url": "https://github.com/openai/openai-python.git",
            "default_branch": "main",
            "language": "Python",
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _DuplicateAfterFetchDB(state)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.connect_repo(
            repo_routes.ConnectRepoRequest(
                github_repo_url="https://github.com/OpenAI/OpenAI-Python"
            ),
            current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.detail == "Repository 'openai/openai-python' is already connected"
    assert "lower(repositories.full_name)" in state["statement"]
    assert db.added == []


@pytest.mark.asyncio
async def test_connect_repo_falls_back_when_github_full_name_is_not_a_string(
    monkeypatch,
) -> None:
    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        return {
            "id": 123,
            "full_name": ["openai/openai-python"],
            "clone_url": "https://github.com/openai/openai-python.git",
            "default_branch": ["main"],
            "language": {"name": "Python"},
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _NoDuplicateDB()
    response = await repo_routes.connect_repo(
        repo_routes.ConnectRepoRequest(
            github_repo_url="https://github.com/OpenAI/OpenAI-Python"
        ),
        current_user=SimpleNamespace(
            id="user-1",
            github_token="gh-token",
            plan="pro",
            plan_display_name="Pro",
        ),
        db=db,
    )

    assert response.full_name == "OpenAI/OpenAI-Python"
    assert response.clone_url == "https://github.com/openai/openai-python.git"
    assert response.default_branch == "main"
    assert response.language is None
    assert len(db.added) == 1
    assert db.added[0].full_name == "OpenAI/OpenAI-Python"


@pytest.mark.asyncio
async def test_connect_repo_falls_back_when_github_full_name_is_invalid(
    monkeypatch,
) -> None:
    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        return {
            "id": 123,
            "full_name": "open ai/openai-python",
            "clone_url": "https://github.com/openai/openai-python.git",
            "default_branch": "main",
            "language": "Python",
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _NoDuplicateDB()
    response = await repo_routes.connect_repo(
        repo_routes.ConnectRepoRequest(
            github_repo_url="https://github.com/OpenAI/OpenAI-Python"
        ),
        current_user=SimpleNamespace(
            id="user-1",
            github_token="gh-token",
            plan="pro",
            plan_display_name="Pro",
        ),
        db=db,
    )

    assert response.full_name == "OpenAI/OpenAI-Python"
    assert len(db.added) == 1
    assert db.added[0].full_name == "OpenAI/OpenAI-Python"


@pytest.mark.asyncio
async def test_connect_repo_handles_user_without_github_token(monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        seen["token"] = token
        return {
            "id": 123,
            "full_name": "openai/openai-python",
            "clone_url": "https://github.com/openai/openai-python.git",
            "default_branch": "main",
            "language": "Python",
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _NoDuplicateDB()
    response = await repo_routes.connect_repo(
        repo_routes.ConnectRepoRequest(
            github_repo_url="https://github.com/openai/openai-python"
        ),
        current_user=SimpleNamespace(
            id="user-1",
            plan="pro",
            plan_display_name="Pro",
        ),
        db=db,
    )

    assert seen["token"] is None
    assert response.full_name == "openai/openai-python"
    assert len(db.added) == 1


@pytest.mark.parametrize(
    "github_token",
    [
        "ghp_valid\r\nX-Injected: value",
        "ghp_valid bad",
    ],
)
@pytest.mark.asyncio
async def test_connect_repo_drops_malformed_github_tokens(
    monkeypatch,
    github_token: str,
) -> None:
    seen: dict[str, object] = {}

    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        seen["token"] = token
        return {
            "id": 123,
            "full_name": "openai/openai-python",
            "clone_url": "https://github.com/openai/openai-python.git",
            "default_branch": "main",
            "language": "Python",
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _NoDuplicateDB()
    response = await repo_routes.connect_repo(
        repo_routes.ConnectRepoRequest(
            github_repo_url="https://github.com/openai/openai-python"
        ),
        current_user=SimpleNamespace(
            id="user-1",
            github_token=github_token,
            plan="pro",
            plan_display_name="Pro",
        ),
        db=db,
    )

    assert seen["token"] is None
    assert response.full_name == "openai/openai-python"
    assert len(db.added) == 1


@pytest.mark.asyncio
async def test_connect_repo_ignores_malformed_github_repo_id(monkeypatch) -> None:
    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        return {
            "id": {"value": 123},
            "full_name": "openai/openai-python",
            "clone_url": "https://github.com/openai/openai-python.git",
            "default_branch": "main",
            "language": "Python",
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _NoDuplicateDB()
    response = await repo_routes.connect_repo(
        repo_routes.ConnectRepoRequest(
            github_repo_url="https://github.com/openai/openai-python"
        ),
        current_user=SimpleNamespace(
            id="user-1",
            github_token="gh-token",
            plan="pro",
            plan_display_name="Pro",
        ),
        db=db,
    )

    assert response.full_name == "openai/openai-python"
    assert len(db.added) == 1
    assert db.added[0].github_repo_id is None


@pytest.mark.asyncio
async def test_connect_repo_rejects_missing_clone_url_from_github(
    monkeypatch,
) -> None:
    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        return {
            "id": 123,
            "full_name": "openai/openai-python",
            "clone_url": {"href": "https://github.com/openai/openai-python.git"},
            "default_branch": "main",
            "language": "Python",
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _NoDuplicateDB()

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.connect_repo(
            repo_routes.ConnectRepoRequest(
                github_repo_url="https://github.com/openai/openai-python"
            ),
            current_user=SimpleNamespace(
                id="user-1",
                github_token="gh-token",
                plan="pro",
                plan_display_name="Pro",
            ),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub repository metadata is missing clone_url."
    assert db.added == []


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://github.com/openai/openai-python.git\r\nbad",
        "https://github.com/openai/openai-python .git",
    ],
)
@pytest.mark.asyncio
async def test_connect_repo_rejects_malformed_clone_url_text_from_github(
    monkeypatch,
    clone_url: str,
) -> None:
    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        return {
            "id": 123,
            "full_name": "openai/openai-python",
            "clone_url": clone_url,
            "default_branch": "main",
            "language": "Python",
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _NoDuplicateDB()

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.connect_repo(
            repo_routes.ConnectRepoRequest(
                github_repo_url="https://github.com/openai/openai-python"
            ),
            current_user=SimpleNamespace(
                id="user-1",
                github_token="gh-token",
                plan="pro",
                plan_display_name="Pro",
            ),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "GitHub repository metadata is missing clone_url."
    assert db.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("clone_url", "expected_detail"),
    [
        (
            "http://github.com/openai/openai-python.git",
            "GitHub repository metadata has an invalid clone_url.",
        ),
        (
            "git@github.com:openai/openai-python.git",
            "GitHub repository metadata has an invalid clone_url.",
        ),
        (
            "ssh://git@github.com/openai/openai-python.git",
            "GitHub repository metadata has an invalid clone_url.",
        ),
        (
            "https://github.com/openai/openai-python.git?access_token=secret",
            "GitHub repository metadata has an invalid clone_url.",
        ),
        (
            "https://github.com/openai/openai-python.git#readme",
            "GitHub repository metadata has an invalid clone_url.",
        ),
        (
            "https://github.com/other/repo.git",
            "GitHub repository metadata clone_url does not match repository.",
        ),
    ],
)
async def test_connect_repo_rejects_bad_clone_url_from_github(
    monkeypatch,
    clone_url: str,
    expected_detail: str,
) -> None:
    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        return {
            "id": 123,
            "full_name": "openai/openai-python",
            "clone_url": clone_url,
            "default_branch": "main",
            "language": "Python",
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _NoDuplicateDB()

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.connect_repo(
            repo_routes.ConnectRepoRequest(
                github_repo_url="https://github.com/openai/openai-python"
            ),
            current_user=SimpleNamespace(
                id="user-1",
                github_token="gh-token",
                plan="pro",
                plan_display_name="Pro",
            ),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == expected_detail
    assert db.added == []


@pytest.mark.asyncio
async def test_connect_repo_enforces_plan_repo_limit(monkeypatch) -> None:
    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        return {
            "id": 123,
            "full_name": "openai/openai-python",
            "clone_url": "https://github.com/openai/openai-python.git",
            "default_branch": "main",
            "language": "Python",
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _RepoLimitDB(repo_count=1)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.connect_repo(
            repo_routes.ConnectRepoRequest(
                github_repo_url="https://github.com/openai/openai-python"
            ),
            current_user=SimpleNamespace(
                id="user-1",
                github_token="gh-token",
                plan="starter",
                plan_display_name="Starter",
            ),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "The Starter plan allows up to 1 connected GitHub repo(s)."
    assert db.added == []


@pytest.mark.asyncio
async def test_connect_repo_tolerates_malformed_repo_limit_count(monkeypatch) -> None:
    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        return {
            "id": 123,
            "full_name": "openai/openai-python",
            "clone_url": "https://github.com/openai/openai-python.git",
            "default_branch": "main",
            "language": "Python",
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _RepoLimitDB(repo_count="not-a-number")
    response = await repo_routes.connect_repo(
        repo_routes.ConnectRepoRequest(
            github_repo_url="https://github.com/openai/openai-python"
        ),
        current_user=SimpleNamespace(
            id="user-1",
            github_token="gh-token",
            plan="starter",
            plan_display_name="Starter",
        ),
        db=db,
    )

    assert response.full_name == "openai/openai-python"
    assert len(db.added) == 1


@pytest.mark.asyncio
async def test_connect_repo_preserves_legacy_enterprise_unlimited_access(
    monkeypatch,
) -> None:
    async def fake_fetch_github_repo_info(full_name: str, token: str | None) -> dict:
        return {
            "id": 123,
            "full_name": "openai/openai-python",
            "clone_url": "https://github.com/openai/openai-python.git",
            "default_branch": "main",
            "language": "Python",
        }

    monkeypatch.setattr(repo_routes, "_fetch_github_repo_info", fake_fetch_github_repo_info)

    db = _NoDuplicateDB()
    response = await repo_routes.connect_repo(
        repo_routes.ConnectRepoRequest(
            github_repo_url="https://github.com/openai/openai-python"
        ),
        current_user=SimpleNamespace(
            id="user-1",
            github_token="gh-token",
            plan="enterprise",
            plan_display_name="Enterprise",
        ),
        db=db,
    )

    assert response.full_name == "openai/openai-python"
    assert len(db.added) == 1
