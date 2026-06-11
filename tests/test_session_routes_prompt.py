from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import codey.saas.api.session_routes as session_routes


class _FakeUser:
    id = "user-1"


class _FakeDB:
    def add(self, _obj) -> None:
        return None

    async def flush(self) -> None:
        return None


class _CaptureDB(_FakeDB):
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def execute(self, _statement):
        return _RepoScalarResult(None)


class _RepoScalarResult:
    def __init__(self, obj) -> None:
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _RepoDB(_FakeDB):
    def __init__(self, repo) -> None:
        self._repo = repo

    async def execute(self, _statement):
        return _RepoScalarResult(self._repo)


def test_prompt_request_rejects_blank_prompts() -> None:
    with pytest.raises(ValidationError):
        session_routes.PromptRequest(prompt="   ", language="python")


def test_prompt_request_strips_prompt_text() -> None:
    request = session_routes.PromptRequest(prompt="  fix this bug  ", language="python")

    assert request.prompt == "fix this bug"


def test_prompt_request_normalizes_blank_language_to_none() -> None:
    request = session_routes.PromptRequest(
        prompt="fix this bug",
        language="   ",
    )

    assert request.language is None


def test_prompt_request_strips_language_text() -> None:
    request = session_routes.PromptRequest(
        prompt="fix this bug",
        language="  typescript  ",
    )

    assert request.language == "typescript"


def test_prompt_request_normalizes_blank_repo_id_to_none() -> None:
    request = session_routes.PromptRequest(
        prompt="fix this bug",
        language="python",
        repo_id="   ",
    )

    assert request.repo_id is None


def test_prompt_request_strips_repo_id_text() -> None:
    repo_id = str(uuid.uuid4())

    request = session_routes.PromptRequest(
        prompt="fix this bug",
        language="python",
        repo_id=f"  {repo_id}  ",
    )

    assert request.repo_id == repo_id


def test_prompt_request_rejects_invalid_repo_id() -> None:
    with pytest.raises(ValidationError):
        session_routes.PromptRequest(
            prompt="fix this bug",
            language="python",
            repo_id="not-a-uuid",
        )


def test_count_generated_lines_ignores_blank_and_trailing_lines() -> None:
    assert session_routes._count_generated_lines("") == 0
    assert session_routes._count_generated_lines("print('ok')\n") == 1
    assert session_routes._count_generated_lines("\nprint('ok')\n\nprint('done')\n") == 2


def test_repo_grounding_context_ignores_malformed_analysis_file_hints() -> None:
    repo_files = ["README.md", "src/app.py", "pyproject.toml"]
    contents = {
        "README.md": "# Repo",
        "src/app.py": "print('ok')",
        "pyproject.toml": "[project]\nname = 'repo'",
    }
    repo_state = SimpleNamespace(
        hotspots=[
            SimpleNamespace(file_path={"path": "README.md"}),
            object(),
            SimpleNamespace(file_path="src/app.py"),
        ],
    )
    candidates = [
        SimpleNamespace(
            title="Malformed target",
            target_file_path=["README.md"],
            predicted_repo_es_delta=0.1,
            risk=0.2,
        ),
        object(),
        SimpleNamespace(
            title="Fix readme",
            target_file_path="README.md",
            predicted_repo_es_delta="0.1234",
            risk="0.25",
        ),
    ]

    def read_text(file_path: str, max_chars: int = 2200) -> str:
        return contents[file_path][:max_chars]

    context = session_routes._build_repo_grounding_context(
        repo_name="owner/repo",
        prompt="Fix repository reliability",
        repo_files=repo_files,
        read_text=read_text,
        repo_state=repo_state,
        candidates=candidates,
    )

    assert "FILE: README.md" in context
    assert "FILE: src/app.py" in context
    assert "- Fix readme -> README.md (delta_ES=0.123, risk=0.25)" in context


def test_extract_prompt_file_hints_preserves_hidden_files_and_rejects_traversal() -> None:
    hints = session_routes._extract_prompt_file_hints(
        "Update `./.prettierrc.json`, ignore `../config.json`, and read src/app.py"
    )

    assert ".prettierrc.json" in hints
    assert "src/app.py" in hints
    assert "../config.json" not in hints
    assert "config.json" not in hints


def test_normalize_prompt_file_hint_rejects_control_characters() -> None:
    assert session_routes._normalize_prompt_file_hint("src/bad\nname.py") is None
    assert session_routes._normalize_prompt_file_hint("src/bad\tname.py") is None
    assert session_routes._normalize_prompt_file_hint("src/bad\x7fname.py") is None
    assert session_routes._normalize_prompt_file_hint("\nsrc/app.py") is None


def test_select_repo_context_files_matches_hidden_file_hints() -> None:
    selected = session_routes._select_repo_context_files(
        prompt="Update `./.prettierrc.json` and ignore `../config.json`",
        repo_files=[".prettierrc.json", "config.json"],
        repo_state=SimpleNamespace(hotspots=[]),
        candidates=[],
    )

    assert selected == [".prettierrc.json"]


def test_select_repo_context_files_skips_ambiguous_basename_hints() -> None:
    selected = session_routes._select_repo_context_files(
        prompt="Fix utils.py and src/app.py",
        repo_files=["src/utils.py", "tests/utils.py", "src/app.py"],
        repo_state=SimpleNamespace(hotspots=[]),
        candidates=[],
    )

    assert selected == ["src/app.py"]


def test_select_repo_context_files_includes_modern_js_module_extensions() -> None:
    selected = session_routes._select_repo_context_files(
        prompt="Update src/index.mjs and `src/types.mts`",
        repo_files=["src/index.mjs", "src/types.mts"],
        repo_state=SimpleNamespace(hotspots=[]),
        candidates=[],
    )

    assert selected == ["src/index.mjs", "src/types.mts"]
    assert session_routes._snippet_language("src/index.mjs") == "javascript"
    assert session_routes._snippet_language("src/types.mts") == "typescript"


def test_extract_prompt_file_hints_matches_full_extensions_not_prefixes() -> None:
    hints = session_routes._extract_prompt_file_hints(
        "Update src/App.jsx and src/page.tsx"
    )

    assert "src/App.jsx" in hints
    assert "src/page.tsx" in hints
    assert "src/App.js" not in hints
    assert "src/page.ts" not in hints


@pytest.mark.asyncio
async def test_create_prompt_session_preserves_http_exceptions_and_refunds(
    monkeypatch,
) -> None:
    refunds: list[tuple[str, int]] = []

    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        @staticmethod
        def estimate_cost(prompt: str, mode: str = "prompt") -> int:
            return 7

        async def reserve_credits(
            self,
            user_id,
            estimated_cost,
            description,
        ) -> None:
            return None

        async def refund_credits(
            self,
            user_id,
            amount,
            description,
        ) -> None:
            refunds.append((str(user_id), amount))

    class _FailingStack:
        async def run(self, *args, **kwargs):
            raise HTTPException(status_code=400, detail="Bad prompt")

    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)
    monkeypatch.setattr(session_routes, "IntelligenceStack", lambda: _FailingStack())

    with pytest.raises(HTTPException) as exc_info:
        await session_routes.create_prompt_session(
            session_routes.PromptRequest(prompt="bad prompt", language="python"),
            current_user=_FakeUser(),
            db=_FakeDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Bad prompt"
    assert refunds == [("user-1", 7)]


@pytest.mark.asyncio
async def test_create_prompt_session_redacts_credentials_from_failure_records(
    monkeypatch,
) -> None:
    refunds: list[tuple[str, int, str]] = []

    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        @staticmethod
        def estimate_cost(prompt: str, mode: str = "prompt") -> int:
            return 7

        async def reserve_credits(
            self,
            user_id,
            estimated_cost,
            description,
        ) -> None:
            return None

        async def refund_credits(
            self,
            user_id,
            amount,
            description,
        ) -> None:
            refunds.append((str(user_id), amount, description))

    class _FailingStack:
        async def run(self, *args, **kwargs):
            raise RuntimeError("provider failed https://user:secret@example.test/repo")

    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)
    monkeypatch.setattr(session_routes, "IntelligenceStack", lambda: _FailingStack())

    db = _CaptureDB()

    with pytest.raises(HTTPException) as exc_info:
        await session_routes.create_prompt_session(
            session_routes.PromptRequest(prompt="bad prompt", language="python"),
            current_user=_FakeUser(),
            db=db,
        )

    assert exc_info.value.status_code == 500
    assert "secret" not in exc_info.value.detail
    assert "https://***@example.test/repo" in exc_info.value.detail
    assert "secret" not in refunds[0][2]
    assert "https://***@example.test/repo" in refunds[0][2]
    assert "secret" not in db.added[0].error_message


@pytest.mark.asyncio
async def test_create_prompt_session_refunds_when_initial_flush_fails(
    monkeypatch,
) -> None:
    refunds: list[tuple[str, int, str]] = []

    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        @staticmethod
        def estimate_cost(prompt: str, mode: str = "prompt") -> int:
            return 7

        async def reserve_credits(
            self,
            user_id,
            estimated_cost,
            description,
        ) -> None:
            return None

        async def refund_credits(
            self,
            user_id,
            amount,
            description,
        ) -> None:
            refunds.append((str(user_id), amount, description))

    class _FailingFlushDB(_CaptureDB):
        async def flush(self) -> None:
            raise RuntimeError("database down https://user:secret@example.test/repo")

    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)

    db = _FailingFlushDB()

    with pytest.raises(HTTPException) as exc_info:
        await session_routes.create_prompt_session(
            session_routes.PromptRequest(prompt="persist this", language="python"),
            current_user=_FakeUser(),
            db=db,
        )

    assert exc_info.value.status_code == 500
    assert "secret" not in exc_info.value.detail
    assert "https://***@example.test/repo" in exc_info.value.detail
    assert refunds[0][:2] == ("user-1", 7)
    assert "secret" not in refunds[0][2]
    assert "https://***@example.test/repo" in refunds[0][2]
    assert db.added[0].status == "failed"
    assert db.added[0].credits_charged == 0
    assert "secret" not in db.added[0].error_message


@pytest.mark.asyncio
async def test_create_prompt_session_preserves_repo_grounding_http_exceptions_and_refunds(
    monkeypatch,
) -> None:
    refunds: list[tuple[str, int]] = []
    repo_id = uuid.uuid4()

    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        @staticmethod
        def estimate_cost(prompt: str, mode: str = "prompt") -> int:
            return 7

        async def reserve_credits(
            self,
            user_id,
            estimated_cost,
            description,
        ) -> None:
            return None

        async def refund_credits(
            self,
            user_id,
            amount,
            description,
        ) -> None:
            refunds.append((str(user_id), amount))

    @asynccontextmanager
    async def fake_cloned_repository(*args, **kwargs):
        raise HTTPException(
            status_code=403,
            detail="GitHub denied repository access. Reconnect GitHub and try again.",
        )
        yield

    repo = type(
        "Repo",
        (),
        {
            "id": repo_id,
            "user_id": "user-1",
            "clone_url": "https://github.com/owner/repo.git",
            "full_name": "owner/repo",
        },
    )()

    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)
    monkeypatch.setattr(session_routes, "cloned_repository", fake_cloned_repository)

    with pytest.raises(HTTPException) as exc_info:
        await session_routes.create_prompt_session(
            session_routes.PromptRequest(
                prompt="Fix the repo issue",
                language="python",
                repo_id=str(repo_id),
            ),
            current_user=type(
                "User",
                (),
                {"id": "user-1", "github_token": "gh-token"},
            )(),
            db=_RepoDB(repo),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == (
        "GitHub denied repository access. Reconnect GitHub and try again."
    )
    assert refunds == [("user-1", 7)]


@pytest.mark.asyncio
async def test_build_repo_nfet_prompt_context_rejects_malformed_clone_url(
    monkeypatch,
) -> None:
    repo_id = uuid.uuid4()
    repo = type(
        "Repo",
        (),
        {
            "id": repo_id,
            "user_id": "user-1",
            "clone_url": {"url": "https://github.com/owner/repo.git"},
            "full_name": "owner/repo",
        },
    )()

    @asynccontextmanager
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("cloned_repository should not be called")
        yield

    monkeypatch.setattr(session_routes, "cloned_repository", fail_if_called)

    with pytest.raises(HTTPException) as exc_info:
        await session_routes._build_repo_nfet_prompt_context(
            str(repo_id),
            "Fix the repo issue",
            current_user=type("User", (), {"id": "user-1", "github_token": "gh-token"})(),
            db=_RepoDB(repo),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Repository has no clone URL configured"


@pytest.mark.asyncio
async def test_build_repo_nfet_prompt_context_rejects_control_character_clone_url(
    monkeypatch,
) -> None:
    repo_id = uuid.uuid4()
    repo = type(
        "Repo",
        (),
        {
            "id": repo_id,
            "user_id": "user-1",
            "clone_url": "https://github.com/owner/repo.git\r\nbad",
            "full_name": "owner/repo",
        },
    )()

    @asynccontextmanager
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("cloned_repository should not be called")
        yield

    monkeypatch.setattr(session_routes, "cloned_repository", fail_if_called)

    with pytest.raises(HTTPException) as exc_info:
        await session_routes._build_repo_nfet_prompt_context(
            str(repo_id),
            "Fix the repo issue",
            current_user=type("User", (), {"id": "user-1", "github_token": "gh-token"})(),
            db=_RepoDB(repo),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Repository has no clone URL configured"


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://github.com/owner/repo.git?access_token=secret",
        "https://github.com/owner/repo.git#readme",
        "https://user:secret@github.com/owner/repo.git",
        "ssh://git:secret@github.com/owner/repo.git",
        "ftp://github.com/owner/repo.git",
        "javascript://github.com/owner/repo.git",
        "https://github.com:not-a-port/owner/repo.git",
        "https:///owner/repo.git",
        "owner/repo",
        "/tmp/repo.git",
        "github.com:owner/repo.git",
        "git@gitlab.com:owner/repo.git",
    ],
)
@pytest.mark.asyncio
async def test_build_repo_nfet_prompt_context_rejects_malformed_clone_url_shapes(
    monkeypatch,
    clone_url: str,
) -> None:
    repo_id = uuid.uuid4()
    repo = type(
        "Repo",
        (),
        {
            "id": repo_id,
            "user_id": "user-1",
            "clone_url": clone_url,
            "full_name": "owner/repo",
        },
    )()

    @asynccontextmanager
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("cloned_repository should not be called")
        yield

    monkeypatch.setattr(session_routes, "cloned_repository", fail_if_called)

    with pytest.raises(HTTPException) as exc_info:
        await session_routes._build_repo_nfet_prompt_context(
            str(repo_id),
            "Fix the repo issue",
            current_user=type("User", (), {"id": "user-1", "github_token": "gh-token"})(),
            db=_RepoDB(repo),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Repository has no clone URL configured"


@pytest.mark.asyncio
async def test_build_repo_nfet_prompt_context_redacts_clone_failure_credentials(
    monkeypatch,
) -> None:
    repo_id = uuid.uuid4()
    repo = SimpleNamespace(
        id=repo_id,
        user_id="user-1",
        clone_url="https://github.com/owner/repo.git",
        full_name="owner/repo",
    )

    @asynccontextmanager
    async def fake_cloned_repository(*args, **kwargs):
        raise RuntimeError(
            "git clone failed: "
            "https://x-access-token:secret@github.com/owner/repo.git"
        )
        yield

    monkeypatch.setattr(session_routes, "cloned_repository", fake_cloned_repository)

    with pytest.raises(HTTPException) as exc_info:
        await session_routes._build_repo_nfet_prompt_context(
            str(repo_id),
            "Fix the repo issue",
            current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
            db=_RepoDB(repo),
        )

    assert exc_info.value.status_code == 502
    assert "secret" not in exc_info.value.detail
    assert "https://***@github.com/owner/repo.git" in exc_info.value.detail


@pytest.mark.asyncio
async def test_build_repo_nfet_prompt_context_allows_public_repo_without_github_token(
    monkeypatch,
) -> None:
    clone_calls: list[tuple[str, str | None]] = []
    repo_id = uuid.uuid4()
    repo = SimpleNamespace(
        id=repo_id,
        user_id="user-1",
        clone_url="https://github.com/owner/repo.git",
        full_name="owner/repo",
    )

    class _RepoBundle:
        graph = object()

        def list_files(self) -> list[str]:
            return ["README.md"]

        def read_text(self, file_path: str, max_chars: int = 2200) -> str:
            assert file_path == "README.md"
            return "# Repo"[:max_chars]

    class _Controller:
        def analyze(self, graph, goal):
            return SimpleNamespace(
                phase="stabilize",
                global_es=0.42,
                hotspots=[],
                components=["README.md"],
                total_nodes=1,
            )

        def rank_interventions(self, graph, goal, repo_state, limit):
            return []

        def build_guidance(self, repo_state, candidates):
            return "guidance"

    @asynccontextmanager
    async def fake_cloned_repository(clone_url: str, token: str | None = None):
        clone_calls.append((clone_url, token))
        yield _RepoBundle()

    monkeypatch.setattr(session_routes, "cloned_repository", fake_cloned_repository)
    monkeypatch.setattr(session_routes, "NFETController", _Controller)

    guidance, grounding, context = await session_routes._build_repo_nfet_prompt_context(
        str(repo_id),
        "Improve repo reliability",
        current_user=SimpleNamespace(id="user-1"),
        db=_RepoDB(repo),
    )

    assert clone_calls == [("https://github.com/owner/repo.git", None)]
    assert guidance == "guidance"
    assert "FILE: README.md" in grounding
    assert context["repo_full_name"] == "owner/repo"


@pytest.mark.asyncio
async def test_create_prompt_session_records_repo_full_name_instead_of_repo_id(
    monkeypatch,
) -> None:
    repo_id = uuid.uuid4()

    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        @staticmethod
        def estimate_cost(prompt: str, mode: str = "prompt") -> int:
            return 7

        async def reserve_credits(self, user_id, estimated_cost, description) -> None:
            return None

        async def refund_credits(self, user_id, amount, description) -> None:
            return None

    async def fake_build_repo_nfet_prompt_context(repo_id, prompt, current_user, db):
        return "", "", {
            "repo_full_name": "owner/repo",
            "nfet_phase_before": "stabilize",
            "nfet_es_before": 0.42,
        }

    class _FakeStack:
        async def run(self, *args, **kwargs):
            return type("Result", (), {"content": "print('ok')", "assessment": None})()

    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)
    monkeypatch.setattr(
        session_routes,
        "_build_repo_nfet_prompt_context",
        fake_build_repo_nfet_prompt_context,
    )
    monkeypatch.setattr(session_routes, "IntelligenceStack", lambda: _FakeStack())

    db = _CaptureDB()

    await session_routes.create_prompt_session(
        session_routes.PromptRequest(
            prompt="Fix the repo issue",
            language="python",
            repo_id=str(repo_id),
        ),
        current_user=type("User", (), {"id": "user-1", "github_token": "gh-token"})(),
        db=db,
    )

    assert db.added[0].repo_connected == "owner/repo"


@pytest.mark.asyncio
async def test_create_prompt_session_ignores_malformed_repo_full_name_in_context(
    monkeypatch,
) -> None:
    repo_id = uuid.uuid4()

    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        @staticmethod
        def estimate_cost(prompt: str, mode: str = "prompt") -> int:
            return 7

        async def reserve_credits(self, user_id, estimated_cost, description) -> None:
            return None

        async def refund_credits(self, user_id, amount, description) -> None:
            return None

    async def fake_build_repo_nfet_prompt_context(repo_id, prompt, current_user, db):
        return "", "", {
            "repo_full_name": {"repo": "owner/repo"},
            "nfet_phase_before": "stabilize",
            "nfet_es_before": 0.42,
        }

    class _FakeStack:
        async def run(self, *args, **kwargs):
            return type("Result", (), {"content": "print('ok')", "assessment": None})()

    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)
    monkeypatch.setattr(
        session_routes,
        "_build_repo_nfet_prompt_context",
        fake_build_repo_nfet_prompt_context,
    )
    monkeypatch.setattr(session_routes, "IntelligenceStack", lambda: _FakeStack())

    db = _CaptureDB()

    await session_routes.create_prompt_session(
        session_routes.PromptRequest(
            prompt="Fix the repo issue",
            language="python",
            repo_id=str(repo_id),
        ),
        current_user=type("User", (), {"id": "user-1", "github_token": "gh-token"})(),
        db=db,
    )

    assert db.added[0].repo_connected is None


@pytest.mark.asyncio
async def test_create_prompt_session_handles_missing_repo_connected_attribute(
    monkeypatch,
) -> None:
    repo_id = uuid.uuid4()

    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        @staticmethod
        def estimate_cost(prompt: str, mode: str = "prompt") -> int:
            return 7

        async def reserve_credits(self, user_id, estimated_cost, description) -> None:
            return None

        async def refund_credits(self, user_id, amount, description) -> None:
            return None

    class _SparseCodingSession:
        def __init__(self, **kwargs) -> None:
            self.id = uuid.uuid4()
            for key, value in kwargs.items():
                if key == "repo_connected" and value is None:
                    continue
                setattr(self, key, value)

    async def fake_build_repo_nfet_prompt_context(repo_id, prompt, current_user, db):
        return "", "", {
            "repo_full_name": {"repo": "owner/repo"},
            "nfet_phase_before": "stabilize",
            "nfet_es_before": 0.42,
        }

    class _FakeStack:
        async def run(self, *args, **kwargs):
            return type("Result", (), {"content": "print('ok')", "assessment": None})()

    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)
    monkeypatch.setattr(session_routes, "CodingSession", _SparseCodingSession)
    monkeypatch.setattr(
        session_routes,
        "_build_repo_nfet_prompt_context",
        fake_build_repo_nfet_prompt_context,
    )
    monkeypatch.setattr(session_routes, "IntelligenceStack", lambda: _FakeStack())

    db = _CaptureDB()

    response = await session_routes.create_prompt_session(
        session_routes.PromptRequest(
            prompt="Fix the repo issue",
            language="python",
            repo_id=str(repo_id),
        ),
        current_user=type("User", (), {"id": "user-1", "github_token": "gh-token"})(),
        db=db,
    )

    session = db.added[0]
    assert response.status == "completed"
    assert response.output == "print('ok')"
    assert session.repo_connected is None


@pytest.mark.asyncio
async def test_create_prompt_session_normalizes_malformed_nfet_context_metrics(
    monkeypatch,
) -> None:
    repo_id = uuid.uuid4()

    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        @staticmethod
        def estimate_cost(prompt: str, mode: str = "prompt") -> int:
            return 7

        async def reserve_credits(self, user_id, estimated_cost, description) -> None:
            return None

        async def refund_credits(self, user_id, amount, description) -> None:
            return None

    async def fake_build_repo_nfet_prompt_context(repo_id, prompt, current_user, db):
        return "", "", {
            "repo_full_name": "owner/repo",
            "nfet_phase_before": {"phase": "stabilize"},
            "nfet_es_before": {"score": 0.42},
        }

    class _FakeStack:
        async def run(self, *args, **kwargs):
            return type("Result", (), {"content": "print('ok')", "assessment": None})()

    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)
    monkeypatch.setattr(
        session_routes,
        "_build_repo_nfet_prompt_context",
        fake_build_repo_nfet_prompt_context,
    )
    monkeypatch.setattr(session_routes, "IntelligenceStack", lambda: _FakeStack())

    db = _CaptureDB()

    await session_routes.create_prompt_session(
        session_routes.PromptRequest(
            prompt="Fix the repo issue",
            language="python",
            repo_id=str(repo_id),
        ),
        current_user=type("User", (), {"id": "user-1", "github_token": "gh-token"})(),
        db=db,
    )

    session = db.added[0]
    assert session.repo_connected == "owner/repo"
    assert session.nfet_phase_before is None
    assert session.es_score_before is None


@pytest.mark.asyncio
async def test_create_prompt_session_accepts_mapping_stack_output(monkeypatch) -> None:
    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        @staticmethod
        def estimate_cost(prompt: str, mode: str = "prompt") -> int:
            return 7

        async def reserve_credits(self, user_id, estimated_cost, description) -> None:
            return None

        async def refund_credits(self, user_id, amount, description) -> None:
            return None

    class _FakeStack:
        async def run(self, *args, **kwargs):
            return type(
                "Result",
                (),
                {"content": {"content": "print('ok')\n"}, "assessment": None},
            )()

    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)
    monkeypatch.setattr(session_routes, "IntelligenceStack", lambda: _FakeStack())

    db = _CaptureDB()

    response = await session_routes.create_prompt_session(
        session_routes.PromptRequest(prompt="Generate code", language="python"),
        current_user=_FakeUser(),
        db=db,
    )

    session = db.added[0]

    assert response.output == "print('ok')\n"
    assert response.lines_generated == 1
    assert response.status == "completed"
    assert session.output_summary == "print('ok')\n"
    assert session.lines_generated == 1
    assert session.status == "completed"


@pytest.mark.asyncio
async def test_create_prompt_session_allows_missing_assessment_attribute(
    monkeypatch,
) -> None:
    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        @staticmethod
        def estimate_cost(prompt: str, mode: str = "prompt") -> int:
            return 7

        async def reserve_credits(self, user_id, estimated_cost, description) -> None:
            return None

        async def refund_credits(self, user_id, amount, description) -> None:
            return None

    class _FakeStack:
        async def run(self, *args, **kwargs):
            return type("Result", (), {"content": "print('ok')"})()

    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)
    monkeypatch.setattr(session_routes, "IntelligenceStack", lambda: _FakeStack())

    db = _CaptureDB()

    response = await session_routes.create_prompt_session(
        session_routes.PromptRequest(prompt="Generate code", language="python"),
        current_user=_FakeUser(),
        db=db,
    )

    assert response.output == "print('ok')"
    assert response.security_score is None
    assert response.security_issues == []
    assert response.status == "completed"
