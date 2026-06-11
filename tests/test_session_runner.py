from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace

import pytest

from codey.saas.sessions.runner import (
    InsufficientCreditsError,
    SessionRunner,
    _coerce_repository_clone_url,
    _session_failure_error_text,
)


class _RepoStub:
    id = "repo-1"
    clone_url = "https://github.com/example/repo.git"
    user = None


class _UserStub:
    github_token = "tok_en"


class _PrivateRepoStub:
    id = "repo-2"
    clone_url = "https://github.com/example/private.git"
    user = _UserStub()


class _BlankTokenUserStub:
    github_token = "   "


class _BlankTokenRepoStub:
    id = "repo-blank-token"
    clone_url = "https://github.com/example/private.git"
    user = _BlankTokenUserStub()


class _ControlTokenUserStub:
    github_token = "tok\r\nX-Injected: value"


class _ControlTokenRepoStub:
    id = "repo-control-token"
    clone_url = "https://github.com/example/private.git"
    user = _ControlTokenUserStub()


class _WhitespaceTokenUserStub:
    github_token = "tok en"


class _WhitespaceTokenRepoStub:
    id = "repo-whitespace-token"
    clone_url = "https://github.com/example/private.git"
    user = _WhitespaceTokenUserStub()


class _MalformedRepoStub:
    id = "repo-3"
    clone_url = {"url": "https://github.com/example/repo.git"}
    user = None


class _ControlCloneUrlRepoStub:
    id = "repo-control-clone-url"
    clone_url = "https://github.com/example/repo.git\r\nbad"
    user = None


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://github.com/example/repo.git?access_token=secret",
        "https://github.com/example/repo.git#readme",
        "https://user:secret@github.com/example/repo.git",
        "ssh://git:secret@github.com/example/repo.git",
        "ssh://root@github.com/example/repo.git",
        "git+ssh://root@github.com/example/repo.git",
        "git://git@github.com/example/repo.git",
        "ftp://github.com/example/repo.git",
        "javascript://github.com/example/repo.git",
        "https://github.com:not-a-port/example/repo.git",
        "https:///example/repo.git",
        "example/repo",
        "/tmp/repo.git",
        "github.com:example/repo.git",
        "git@gitlab.com:example/repo.git",
        "https://github.com/example/repo.git bad",
    ],
)
def test_coerce_repository_clone_url_rejects_malformed_url_shapes(
    clone_url: str,
) -> None:
    assert _coerce_repository_clone_url(clone_url) is None


@pytest.mark.parametrize(
    "clone_url",
    [
        "git@github.com:example/repo.git",
        "ssh://git@github.com/example/repo.git",
        "git+ssh://git@github.com/example/repo.git",
    ],
)
def test_coerce_repository_clone_url_accepts_safe_ssh_git_urls(
    clone_url: str,
) -> None:
    assert _coerce_repository_clone_url(clone_url) == clone_url


class _HangingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.killed:
            return b"", b""
        await asyncio.sleep(3600)
        return b"", b""

    def kill(self) -> None:
        self.killed = True


class _RaceExitedProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.communicated = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communicated = True
        return b"", b""

    def kill(self) -> None:
        raise ProcessLookupError()


class _FailingDrainProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.drain_attempted = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.killed:
            self.drain_attempted = True
            raise RuntimeError("drain failed")
        await asyncio.sleep(3600)
        return b"", b""

    def kill(self) -> None:
        self.killed = True


class _CompletedProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


class _FailedProcess:
    returncode = 128

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b"fatal: bad byte \xff"


class _StdoutOnlyFailedProcess:
    returncode = 128

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"fatal: repository not found", b""


class _AuthUrlFailedProcess:
    returncode = 128

    async def communicate(self) -> tuple[bytes, bytes]:
        return (
            b"",
            b"fatal: could not read from "
            b"https://x-access-token:tok%20en@github.com/example/private.git",
        )


class _ScalarResult:
    def __init__(self, value) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


@pytest.mark.asyncio
async def test_run_prompt_session_streams_error_for_malformed_ids() -> None:
    sent_messages: list[tuple[str, dict]] = []

    class _FakeStream:
        async def send_to_session(self, session_id, message) -> None:
            sent_messages.append((session_id, message))

    runner = SessionRunner(stream=_FakeStream())

    await runner.run_prompt_session(
        "not-a-uuid",
        "user-1",
        "build a thing",
        None,
        None,
        db=object(),
    )

    assert sent_messages == [
        (
            "not-a-uuid",
            {"type": "error", "message": "ValueError: Invalid session or user ID"},
        )
    ]


@pytest.mark.asyncio
async def test_run_analyze_session_streams_error_for_malformed_ids() -> None:
    sent_messages: list[tuple[str, dict]] = []

    class _FakeStream:
        async def send_to_session(self, session_id, message) -> None:
            sent_messages.append((session_id, message))

    runner = SessionRunner(stream=_FakeStream())

    await runner.run_analyze_session(
        "not-a-uuid",
        "user-1",
        [],
        db=object(),
    )

    assert sent_messages == [
        (
            "not-a-uuid",
            {"type": "error", "message": "ValueError: Invalid session or user ID"},
        )
    ]


@pytest.mark.asyncio
async def test_get_session_scopes_lookup_to_user_id() -> None:
    session = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    captured: dict[str, str] = {}

    class _DB:
        async def execute(self, statement):
            captured["statement"] = str(statement)
            return _ScalarResult(session)

    runner = SessionRunner(stream=object())

    result = await runner._get_session(_DB(), session_id, user_id)

    assert result is session
    assert "coding_sessions.id" in captured["statement"]
    assert "coding_sessions.user_id" in captured["statement"]


@pytest.mark.asyncio
async def test_get_repository_scopes_lookup_to_user_id() -> None:
    repo = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    repo_id = uuid.uuid4()
    user_id = uuid.uuid4()
    captured: dict[str, str] = {}

    class _DB:
        async def execute(self, statement):
            captured["statement"] = str(statement)
            return _ScalarResult(repo)

    runner = SessionRunner(stream=object())

    result = await runner._get_repository(_DB(), repo_id, user_id)

    assert result is repo
    assert "repositories.id" in captured["statement"]
    assert "repositories.user_id" in captured["statement"]


@pytest.mark.asyncio
async def test_parse_repository_times_out_and_kills_clone(monkeypatch) -> None:
    runner = SessionRunner(stream=object())
    process = _HangingProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    wait_for_calls = 0

    async def fake_wait_for(awaitable, timeout):
        nonlocal wait_for_calls
        wait_for_calls += 1
        if wait_for_calls > 1:
            return await awaitable
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    with pytest.raises(RuntimeError, match="git clone timed out"):
        await runner._parse_repository(_RepoStub())

    assert process.killed is True


@pytest.mark.asyncio
async def test_parse_repository_tolerates_kill_race_on_clone_timeout(monkeypatch) -> None:
    runner = SessionRunner(stream=object())
    process = _RaceExitedProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    wait_for_calls = 0

    async def fake_wait_for(awaitable, timeout):
        nonlocal wait_for_calls
        wait_for_calls += 1
        if wait_for_calls > 1:
            return await awaitable
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    with pytest.raises(RuntimeError, match="git clone timed out"):
        await runner._parse_repository(_RepoStub())

    assert process.communicated is True


@pytest.mark.asyncio
async def test_parse_repository_preserves_timeout_when_clone_drain_fails(
    monkeypatch,
) -> None:
    runner = SessionRunner(stream=object())
    process = _FailingDrainProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    wait_for_calls = 0

    async def fake_wait_for(awaitable, timeout):
        nonlocal wait_for_calls
        wait_for_calls += 1
        if wait_for_calls > 1:
            return await awaitable
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    with pytest.raises(RuntimeError, match="git clone timed out"):
        await runner._parse_repository(_RepoStub())

    assert process.killed is True
    assert process.drain_attempted is True


@pytest.mark.asyncio
async def test_parse_repository_uses_authenticated_clone_url(monkeypatch) -> None:
    runner = SessionRunner(stream=object())
    captured_args: tuple | None = None

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal captured_args
        captured_args = args
        return _CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("codey.saas.sessions.runner.parse_directory", lambda path: ([], []))

    nodes, edges = await runner._parse_repository(_PrivateRepoStub())

    assert nodes == []
    assert edges == []
    assert captured_args is not None
    assert captured_args[:6] == (
        "git",
        "clone",
        "--depth",
        "1",
        "--",
        "https://x-access-token:tok_en@github.com/example/private.git",
    )


@pytest.mark.asyncio
async def test_parse_repository_disables_interactive_git_credentials(
    monkeypatch,
) -> None:
    runner = SessionRunner(stream=object())
    captured_kwargs: dict | None = None

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal captured_kwargs
        captured_kwargs = kwargs
        return _CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("codey.saas.sessions.runner.parse_directory", lambda path: ([], []))

    await runner._parse_repository(_RepoStub())

    assert captured_kwargs is not None
    env = captured_kwargs["env"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"


@pytest.mark.asyncio
async def test_parse_repository_ignores_blank_github_token(monkeypatch) -> None:
    runner = SessionRunner(stream=object())
    captured_args: tuple | None = None

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal captured_args
        captured_args = args
        return _CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("codey.saas.sessions.runner.parse_directory", lambda path: ([], []))

    nodes, edges = await runner._parse_repository(_BlankTokenRepoStub())

    assert nodes == []
    assert edges == []
    assert captured_args is not None
    assert captured_args[:6] == (
        "git",
        "clone",
        "--depth",
        "1",
        "--",
        "https://github.com/example/private.git",
    )


@pytest.mark.asyncio
async def test_parse_repository_ignores_control_character_github_token(
    monkeypatch,
) -> None:
    runner = SessionRunner(stream=object())
    captured_args: tuple | None = None

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal captured_args
        captured_args = args
        return _CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("codey.saas.sessions.runner.parse_directory", lambda path: ([], []))

    nodes, edges = await runner._parse_repository(_ControlTokenRepoStub())

    assert nodes == []
    assert edges == []
    assert captured_args is not None
    assert captured_args[:6] == (
        "git",
        "clone",
        "--depth",
        "1",
        "--",
        "https://github.com/example/private.git",
    )


@pytest.mark.asyncio
async def test_parse_repository_ignores_whitespace_github_token(monkeypatch) -> None:
    runner = SessionRunner(stream=object())
    captured_args: tuple | None = None

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal captured_args
        captured_args = args
        return _CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("codey.saas.sessions.runner.parse_directory", lambda path: ([], []))

    nodes, edges = await runner._parse_repository(_WhitespaceTokenRepoStub())

    assert nodes == []
    assert edges == []
    assert captured_args is not None
    assert captured_args[:6] == (
        "git",
        "clone",
        "--depth",
        "1",
        "--",
        "https://github.com/example/private.git",
    )


@pytest.mark.asyncio
async def test_parse_repository_replaces_non_utf8_clone_stderr(monkeypatch) -> None:
    runner = SessionRunner(stream=object())

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FailedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError, match="git clone failed \\(exit 128\\): fatal: bad byte"):
        await runner._parse_repository(_RepoStub())


@pytest.mark.asyncio
async def test_parse_repository_falls_back_to_stdout_when_clone_stderr_is_empty(
    monkeypatch,
) -> None:
    runner = SessionRunner(stream=object())

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _StdoutOnlyFailedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError, match="git clone failed \\(exit 128\\): fatal: repository not found"):
        await runner._parse_repository(_RepoStub())


@pytest.mark.asyncio
async def test_parse_repository_redacts_authenticated_clone_url_errors(
    monkeypatch,
) -> None:
    runner = SessionRunner(stream=object())

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _AuthUrlFailedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError) as exc_info:
        await runner._parse_repository(_PrivateRepoStub())

    message = str(exc_info.value)
    assert "tok%20en" not in message
    assert "https://***@github.com/example/private.git" in message


@pytest.mark.asyncio
async def test_parse_repository_rejects_non_string_clone_url(monkeypatch) -> None:
    runner = SessionRunner(stream=object())

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise AssertionError("git clone should not run for malformed clone_url")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(ValueError, match="Repository repo-3 has no clone_url"):
        await runner._parse_repository(_MalformedRepoStub())


@pytest.mark.asyncio
async def test_parse_repository_rejects_control_character_clone_url(
    monkeypatch,
) -> None:
    runner = SessionRunner(stream=object())

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise AssertionError("git clone should not run for malformed clone_url")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(
        ValueError,
        match="Repository repo-control-clone-url has no clone_url",
    ):
        await runner._parse_repository(_ControlCloneUrlRepoStub())


@pytest.mark.asyncio
async def test_parse_repository_rejects_malformed_clone_url_before_git(
    monkeypatch,
) -> None:
    runner = SessionRunner(stream=object())
    repo = SimpleNamespace(
        id="repo-query-clone-url",
        clone_url="https://github.com/example/repo.git?access_token=secret",
        user=None,
    )

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise AssertionError("git clone should not run for malformed clone_url")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(
        ValueError,
        match="Repository repo-query-clone-url has no clone_url",
    ):
        await runner._parse_repository(repo)


@pytest.mark.asyncio
async def test_parse_repository_reports_missing_git_executable(monkeypatch) -> None:
    runner = SessionRunner(stream=object())

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError, match="git executable not found"):
        await runner._parse_repository(_RepoStub())


@pytest.mark.asyncio
async def test_parse_repository_reports_git_startup_failures(monkeypatch) -> None:
    runner = SessionRunner(stream=object())

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise OSError("process table full")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError, match="failed to start git clone: process table full"):
        await runner._parse_repository(_RepoStub())


@pytest.mark.asyncio
async def test_parse_repository_redacts_git_startup_error_credentials(
    monkeypatch,
) -> None:
    runner = SessionRunner(stream=object())

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise OSError(
            "failed for https://x-access-token:secret@github.com/example/private.git"
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError) as exc_info:
        await runner._parse_repository(_PrivateRepoStub())

    message = str(exc_info.value)
    assert "secret" not in message
    assert "https://***@github.com/example/private.git" in message


@pytest.mark.asyncio
async def test_handle_failure_rolls_back_before_persisting_failure() -> None:
    events: list[str] = []
    session = SimpleNamespace()

    class _FakeDB:
        async def rollback(self) -> None:
            events.append("rollback")

        async def flush(self) -> None:
            events.append("flush")

        async def commit(self) -> None:
            events.append("commit")

    class _FakeCreditService:
        async def refund_credits(self, *_args) -> None:
            events.append("refund")

    class _FakeStream:
        async def send_to_session(self, _session_id, message) -> None:
            events.append(f"send:{message['type']}")

    runner = SessionRunner(stream=_FakeStream())

    async def fake_get_session(_db, _session_id, _user_id):
        events.append("get_session")
        return session

    runner._get_session = fake_get_session

    await runner._handle_failure(
        _FakeDB(),
        uuid.uuid4(),
        uuid.uuid4(),
        2,
        _FakeCreditService(),
        "session-1",
        RuntimeError("boom"),
    )

    assert events == ["rollback", "get_session", "refund", "flush", "commit", "send:error"]
    assert session.status == "failed"
    assert session.error_message == "RuntimeError: boom"


@pytest.mark.asyncio
async def test_handle_failure_redacts_credentialed_urls_from_error_message() -> None:
    session = SimpleNamespace()
    sent_messages: list[dict] = []

    class _FakeDB:
        async def rollback(self) -> None:
            return None

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

    class _FakeCreditService:
        async def refund_credits(self, *_args) -> None:
            return None

    class _FakeStream:
        async def send_to_session(self, _session_id, message) -> None:
            sent_messages.append(message)

    runner = SessionRunner(stream=_FakeStream())

    async def fake_get_session(_db, _session_id, _user_id):
        return session

    runner._get_session = fake_get_session

    await runner._handle_failure(
        _FakeDB(),
        uuid.uuid4(),
        uuid.uuid4(),
        0,
        _FakeCreditService(),
        "session-1",
        RuntimeError("clone failed for https://user:secret@example.com/repo.git"),
    )

    assert session.error_message == (
        "RuntimeError: clone failed for https://***@example.com/repo.git"
    )
    assert sent_messages == [
        {
            "type": "error",
            "message": "RuntimeError: clone failed for https://***@example.com/repo.git",
        }
    ]


def test_session_failure_error_text_redacts_credentialed_urls() -> None:
    error = _session_failure_error_text(
        RuntimeError("clone failed for https://user:secret@example.com/repo.git")
    )

    assert error == "RuntimeError: clone failed for https://***@example.com/repo.git"


@pytest.mark.asyncio
async def test_handle_failure_redacts_secondary_failure_logs(caplog) -> None:
    sent_messages: list[dict] = []

    class _FakeDB:
        async def rollback(self) -> None:
            raise RuntimeError(
                "rollback failed https://user:url-secret@example.com/repo.git"
                "?access_token=query-secret authorization=Bearer bearer-secret "
                "for operator@example.test"
            )

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

    class _FakeCreditService:
        async def refund_credits(self, *_args) -> None:
            return None

    class _FakeStream:
        async def send_to_session(self, _session_id, message) -> None:
            sent_messages.append(message)

    runner = SessionRunner(stream=_FakeStream())

    async def fail_get_session(_db, _session_id, _user_id):
        raise RuntimeError(
            "persist failed https://user:persist-secret@example.com/repo.git"
            "?client_secret=client-secret authorization=Bearer persist-bearer"
        )

    runner._get_session = fail_get_session
    caplog.set_level(logging.WARNING, logger="codey.saas.sessions.runner")

    await runner._handle_failure(
        _FakeDB(),
        uuid.uuid4(),
        uuid.uuid4(),
        0,
        _FakeCreditService(),
        "session-1",
        RuntimeError("root failure"),
    )

    assert sent_messages == [{"type": "error", "message": "RuntimeError: root failure"}]
    assert "url-secret" not in caplog.text
    assert "query-secret" not in caplog.text
    assert "bearer-secret" not in caplog.text
    assert "operator@example.test" not in caplog.text
    assert "persist-secret" not in caplog.text
    assert "client-secret" not in caplog.text
    assert "persist-bearer" not in caplog.text
    assert "https://***@example.com/repo.git" in caplog.text
    assert "access_token=***" in caplog.text
    assert "client_secret=***" in caplog.text
    assert "authorization=Bearer ***" in caplog.text
    assert "[redacted-email]" in caplog.text


@pytest.mark.asyncio
async def test_prompt_session_logs_redacted_failure_without_traceback(caplog) -> None:
    runner = SessionRunner(stream=SimpleNamespace())
    failures: list[Exception] = []

    async def fail_get_session(_db, _session_id, _user_id):
        raise RuntimeError(
            "clone failed for https://user:secret@example.com/repo.git"
        )

    async def record_failure(
        _db,
        _session_id,
        _user_id,
        _reserved_credits,
        _credit_svc,
        _ws_session_id,
        exc,
    ) -> None:
        failures.append(exc)

    runner._get_session = fail_get_session
    runner._handle_failure = record_failure
    caplog.set_level(logging.WARNING, logger="codey.saas.sessions.runner")

    await runner.run_prompt_session(
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        "prompt",
        "python",
        None,
        SimpleNamespace(),
    )

    assert len(failures) == 1
    assert "secret" not in caplog.text
    assert "https://***@example.com/repo.git" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_handle_failure_caps_streamed_and_persisted_error_messages() -> None:
    session = SimpleNamespace()
    sent_messages: list[dict] = []

    class _FakeDB:
        async def rollback(self) -> None:
            return None

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

    class _FakeCreditService:
        async def refund_credits(self, *_args) -> None:
            return None

    class _FakeStream:
        async def send_to_session(self, _session_id, message) -> None:
            sent_messages.append(message)

    runner = SessionRunner(stream=_FakeStream())

    async def fake_get_session(_db, _session_id, _user_id):
        return session

    runner._get_session = fake_get_session

    await runner._handle_failure(
        _FakeDB(),
        uuid.uuid4(),
        uuid.uuid4(),
        0,
        _FakeCreditService(),
        "session-1",
        RuntimeError("x" * 2000),
    )

    assert len(session.error_message) == 1000
    assert sent_messages[0]["message"] == session.error_message


@pytest.mark.asyncio
async def test_reserve_prompt_credits_returns_zero_when_partial_charge_races() -> None:
    runner = SessionRunner(stream=object())
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()

    class _FakeCreditService:
        def __init__(self) -> None:
            self.reserve_calls: list[tuple[int, str]] = []

        async def reserve_credits(
            self,
            _user_id,
            estimated_cost,
            description,
            _session_id,
        ) -> None:
            assert _user_id == user_id
            assert _session_id == session_id
            self.reserve_calls.append((estimated_cost, description))
            raise InsufficientCreditsError(required=estimated_cost, available=0)

        async def get_balance(self, _user_id) -> dict[str, int]:
            assert _user_id == user_id
            return {"total": 2}

    credit_svc = _FakeCreditService()

    reserved = await runner._reserve_prompt_credits(
        credit_svc,
        user_id,
        session_id,
        "session-visible-id",
        charged=5,
        total_lines=40,
    )

    assert reserved == 0
    assert credit_svc.reserve_calls == [
        (5, "Session session-visible-id: 40 lines generated"),
        (2, "Session session-visible-id: partial charge"),
    ]


def test_coerce_available_credits_rejects_malformed_balances() -> None:
    assert SessionRunner._coerce_available_credits({"total": "3"}) == 3
    assert SessionRunner._coerce_available_credits({"total": 0}) == 0
    assert SessionRunner._coerce_available_credits({"total": True}) == 0
    assert SessionRunner._coerce_available_credits({"total": {"credits": 3}}) == 0
    assert SessionRunner._coerce_available_credits(None) == 0


def test_coerce_generated_text_rejects_non_string_values() -> None:
    assert SessionRunner._coerce_generated_text("print('ok')") == "print('ok')"
    assert SessionRunner._coerce_generated_text(None) == ""
    assert SessionRunner._coerce_generated_text({"code": "print('bad')"}) == ""


def test_coerce_generated_result_rejects_non_mapping_values() -> None:
    result = {"code": "print('ok')"}

    assert SessionRunner._coerce_generated_result(result) is result
    assert SessionRunner._coerce_generated_result(None) == {}
    assert SessionRunner._coerce_generated_result(["not", "a", "mapping"]) == {}


def test_generated_temp_suffix_rejects_control_character_suffixes() -> None:
    assert SessionRunner._generated_temp_suffix("app.py\x00", "python") == ".py"
    assert SessionRunner._generated_temp_suffix("app.p\ny", "python") == ".py"
    assert SessionRunner._generated_temp_suffix("app.p\ty", "python") == ".py"
    assert SessionRunner._generated_temp_suffix("app.p\x7fy", "python") == ".py"
    assert SessionRunner._generated_temp_suffix("src/app.ts", "python") == ".ts"
    assert SessionRunner._generated_temp_suffix("Makefile", "typescript") == ".ts"


def test_split_code_into_files_normalizes_safe_marker_paths() -> None:
    code = "# --- file: .\\src\\app.py ---\nprint('ok')\n"

    files = SessionRunner._split_code_into_files(code, "python")

    assert files == {"src/app.py": "print('ok')"}


def test_split_code_into_files_unwraps_quoted_marker_paths() -> None:
    code = "# --- file: `src/app.py` ---\nprint('ok')\n"

    files = SessionRunner._split_code_into_files(code, "python")

    assert files == {"src/app.py": "print('ok')"}


def test_split_code_into_files_replaces_unsafe_marker_paths() -> None:
    code = (
        "# --- file: ../secrets.py ---\nprint('one')\n"
        "# --- file: src/bad" + "\x00" + "name.py ---\nprint('two')\n"
        "# --- file: src/app.py:ads ---\nprint('three')\n"
    )

    files = SessionRunner._split_code_into_files(code, "python")

    assert files == {
        "generated.py": "print('one')",
        "generated_2.py": "print('two')",
        "generated_3.py": "print('three')",
    }


def test_split_code_into_files_preserves_repeated_safe_marker_paths() -> None:
    code = (
        "# --- file: src/app.py ---\nprint('one')\n"
        "# --- file: src/app.py ---\nprint('two')\n"
    )

    files = SessionRunner._split_code_into_files(code, "python")

    assert files == {"src/app.py": "print('one')\n\nprint('two')"}
