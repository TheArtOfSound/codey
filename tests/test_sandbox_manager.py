from __future__ import annotations

import asyncio
import logging
import zipfile
from pathlib import Path

import pytest

import codey.saas.sandbox.manager as sandbox_manager
from codey.saas.archive_utils import MAX_ARCHIVE_PATH_CHARS
from codey.saas.sandbox.manager import (
    CommandResult,
    LocalSandboxBackend,
    Sandbox,
    SandboxManager,
    _sandbox_root_for_id,
)


class _HangingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.communicate_calls = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communicate_calls += 1
        if self.killed:
            return b"", b""
        await asyncio.sleep(3600)
        return b"", b""

    def kill(self) -> None:
        self.killed = True


class _RaceExitedProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.communicate_calls = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communicate_calls += 1
        return b"", b""

    def kill(self) -> None:
        raise ProcessLookupError()


class _FailingDrainProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.communicate_calls = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communicate_calls += 1
        raise RuntimeError("drain failed https://user:secret@example.test/repo.git")

    def kill(self) -> None:
        self.killed = True


class _HangingDrainProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.killed:
            await asyncio.sleep(3600)
        return b"", b""

    def kill(self) -> None:
        self.killed = True


class _FailingKillProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.kill_attempted = False
        self.communicate_calls = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communicate_calls += 1
        await asyncio.sleep(3600)
        return b"", b""

    def kill(self) -> None:
        self.kill_attempted = True
        raise PermissionError("cannot kill https://user:secret@example.test/repo.git")


class _CompletedProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"ok", b""


class _SyncClosableSandbox:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _AsyncClosableSandbox:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FailingBackend:
    async def destroy(self, sandbox: Sandbox) -> None:
        raise RuntimeError(
            "backend destroy failed https://user:secret@example.test/repo.git"
        )


class _CapturingBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.created_timeout: int | None = None
        self.executed_timeout: int | None = None

    async def create(
        self,
        sandbox_id: str,
        user_id: str,
        session_id: str,
        timeout: int,
    ) -> Sandbox:
        self.created_timeout = timeout
        return Sandbox(
            id=sandbox_id,
            user_id=user_id,
            session_id=session_id,
            root=self.root / sandbox_id,
            timeout=timeout,
        )

    async def destroy(self, sandbox: Sandbox) -> None:
        return None

    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout: int,
        env: dict[str, str] | None,
    ) -> CommandResult:
        self.executed_timeout = timeout
        return CommandResult(exit_code=0, stdout="", stderr="")


def test_coerce_sandbox_timeout_rejects_malformed_values() -> None:
    assert sandbox_manager._coerce_sandbox_timeout(True, default=9) == 9
    assert sandbox_manager._coerce_sandbox_timeout(float("nan"), default=9) == 9
    assert sandbox_manager._coerce_sandbox_timeout(float("inf"), default=9) == 9
    assert sandbox_manager._coerce_sandbox_timeout(-1, default=9) == 9
    assert sandbox_manager._coerce_sandbox_timeout("3", default=9) == 3
    assert (
        sandbox_manager._coerce_sandbox_timeout(10**9)
        == sandbox_manager.MAX_TIMEOUT
    )


@pytest.mark.asyncio
async def test_manager_create_coerces_invalid_timeout_before_backend(
    tmp_path: Path,
) -> None:
    manager = SandboxManager.__new__(SandboxManager)
    backend = _CapturingBackend(tmp_path)
    manager._backend = backend
    manager._sandboxes = {}

    sandbox = await manager.create("user", "session", timeout=float("nan"))

    assert backend.created_timeout == sandbox_manager.DEFAULT_TIMEOUT
    assert sandbox.timeout == sandbox_manager.DEFAULT_TIMEOUT


@pytest.mark.asyncio
async def test_manager_execute_coerces_invalid_and_oversized_timeouts(
    tmp_path: Path,
) -> None:
    manager = SandboxManager.__new__(SandboxManager)
    backend = _CapturingBackend(tmp_path)
    manager._backend = backend
    manager._sandboxes = {
        "sandbox": Sandbox(
            id="sandbox",
            user_id="user",
            session_id="session",
            root=tmp_path / "sandbox",
            timeout=45,
        )
    }

    await manager.execute("sandbox", "echo ok", timeout=float("nan"))
    assert backend.executed_timeout == 45

    await manager.execute("sandbox", "echo ok", timeout=10**9)
    assert backend.executed_timeout == sandbox_manager.MAX_TIMEOUT


@pytest.mark.asyncio
async def test_local_backend_execute_coerces_invalid_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = LocalSandboxBackend()
    root = tmp_path / "sandbox"
    (root / "workspace").mkdir(parents=True)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
        timeout=17,
    )
    captured_timeouts: list[int] = []

    async def fake_create_subprocess_shell(*args, **kwargs):
        return _CompletedProcess()

    async def fake_wait_for(awaitable, timeout):
        captured_timeouts.append(timeout)
        return await awaitable

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create_subprocess_shell)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    result = await backend.execute(
        sandbox,
        "echo ok",
        timeout=float("nan"),
        env=None,
    )

    assert result.exit_code == 0
    assert captured_timeouts == [17]


@pytest.mark.asyncio
async def test_local_backend_create_writes_gitignore_as_utf8(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sandbox_manager, "SANDBOX_ROOT", tmp_path / "sandboxes")
    original_write_text = Path.write_text
    write_encodings: list[tuple[str, str | None]] = []

    def recording_write_text(self: Path, data: str, *args, **kwargs) -> int:
        if self.name == ".gitignore":
            write_encodings.append((data, kwargs.get("encoding")))
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", recording_write_text)

    sandbox = await LocalSandboxBackend().create("sandbox", "user", "session", 60)

    assert sandbox.workspace == (
        tmp_path / "sandboxes" / "sandbox" / "workspace"
    ).resolve()
    assert write_encodings == [
        ("__pycache__/\n*.pyc\nnode_modules/\n.env\nvenv/\n", "utf-8")
    ]


@pytest.mark.asyncio
async def test_local_backend_create_redacts_user_and_session_logs(
    caplog,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sandbox_manager, "SANDBOX_ROOT", tmp_path / "sandboxes")
    caplog.set_level(logging.INFO, logger="codey.saas.sandbox.manager")

    sandbox = await LocalSandboxBackend().create(
        "sandbox",
        "https://user:user-secret@example.test/u?token=user-token",
        "https://session:session-secret@example.test/s?token=session-token",
        60,
    )

    assert sandbox.id == "sandbox"
    assert "user-secret" not in caplog.text
    assert "user-token" not in caplog.text
    assert "session-secret" not in caplog.text
    assert "session-token" not in caplog.text
    assert "https://***@example.test/u?token=***" in caplog.text
    assert "https://***@example.test/s?token=***" in caplog.text


@pytest.mark.asyncio
async def test_e2b_backend_execute_coerces_invalid_timeout(tmp_path: Path) -> None:
    from codey.saas.sandbox.manager import E2BSandboxBackend

    class _FakeProcess:
        def __init__(self) -> None:
            self.timeout: int | None = None

        def start_and_wait(self, command, *, timeout, env_vars):
            self.timeout = timeout
            return type(
                "E2BResult",
                (),
                {"exit_code": 0, "stdout": "ok", "stderr": ""},
            )()

    process = _FakeProcess()
    backend = E2BSandboxBackend.__new__(E2BSandboxBackend)
    backend._e2b_sandboxes = {"sandbox": type("E2B", (), {"process": process})()}
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=tmp_path / "sandbox",
        timeout=23,
    )

    result = await backend.execute(
        sandbox,
        "echo ok",
        timeout=float("nan"),
        env=None,
    )

    assert result.exit_code == 0
    assert process.timeout == 23


@pytest.mark.asyncio
async def test_e2b_backend_execute_normalizes_malformed_result_fields(
    tmp_path: Path,
) -> None:
    from codey.saas.sandbox.manager import E2BSandboxBackend

    class _FakeProcess:
        def start_and_wait(self, command, *, timeout, env_vars):
            return type(
                "E2BResult",
                (),
                {"exit_code": "oops", "stdout": b"ok", "stderr": None},
            )()

    backend = E2BSandboxBackend.__new__(E2BSandboxBackend)
    backend._e2b_sandboxes = {"sandbox": type("E2B", (), {"process": _FakeProcess()})()}
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=tmp_path / "sandbox",
        timeout=23,
    )

    result = await backend.execute(
        sandbox,
        "echo ok",
        timeout=1,
        env=None,
    )

    assert result.exit_code == -1
    assert result.stdout == "ok"
    assert result.stderr == ""


def test_select_backend_ignores_whitespace_e2b_api_key(monkeypatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "   ")

    class _UnexpectedE2BBackend:
        def __init__(self) -> None:
            raise AssertionError("E2B backend should not be constructed")

    monkeypatch.setattr(sandbox_manager, "E2BSandboxBackend", _UnexpectedE2BBackend)

    backend = sandbox_manager._select_backend()

    assert isinstance(backend, LocalSandboxBackend)


def test_select_backend_ignores_internal_whitespace_e2b_api_key(monkeypatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "e2b bad")

    class _UnexpectedE2BBackend:
        def __init__(self) -> None:
            raise AssertionError("E2B backend should not be constructed")

    monkeypatch.setattr(sandbox_manager, "E2BSandboxBackend", _UnexpectedE2BBackend)

    backend = sandbox_manager._select_backend()

    assert isinstance(backend, LocalSandboxBackend)


def test_select_backend_ignores_control_character_e2b_api_key(monkeypatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "e2b\tbad")

    class _UnexpectedE2BBackend:
        def __init__(self) -> None:
            raise AssertionError("E2B backend should not be constructed")

    monkeypatch.setattr(sandbox_manager, "E2BSandboxBackend", _UnexpectedE2BBackend)

    backend = sandbox_manager._select_backend()

    assert isinstance(backend, LocalSandboxBackend)


@pytest.mark.asyncio
async def test_local_sandbox_execute_kills_and_drains_timed_out_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = LocalSandboxBackend()
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=tmp_path,
    )
    process = _HangingProcess()

    async def fake_create_subprocess_shell(*args, **kwargs):
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

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create_subprocess_shell)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    result = await backend.execute(
        sandbox,
        "sleep 60",
        timeout=1,
        env=None,
    )

    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.stderr == "Command timed out"
    assert process.killed is True
    assert process.communicate_calls == 1


@pytest.mark.asyncio
async def test_local_sandbox_execute_tolerates_kill_race_on_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = LocalSandboxBackend()
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=tmp_path,
    )
    process = _RaceExitedProcess()

    async def fake_create_subprocess_shell(*args, **kwargs):
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

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create_subprocess_shell)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    result = await backend.execute(
        sandbox,
        "sleep 60",
        timeout=1,
        env=None,
    )

    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.stderr == "Command timed out"
    assert process.communicate_calls == 1


@pytest.mark.asyncio
async def test_local_sandbox_execute_returns_timeout_when_drain_fails(
    monkeypatch,
    caplog,
    tmp_path: Path,
) -> None:
    backend = LocalSandboxBackend()
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=tmp_path,
    )
    process = _FailingDrainProcess()

    async def fake_create_subprocess_shell(*args, **kwargs):
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

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create_subprocess_shell)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    caplog.set_level(logging.WARNING, logger="codey.saas.sandbox.manager")

    result = await backend.execute(
        sandbox,
        "sleep 60",
        timeout=1,
        env=None,
    )

    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.stderr == "Command timed out"
    assert process.killed is True
    assert process.communicate_calls == 1
    assert "secret" not in caplog.text
    assert "https://***@example.test/repo.git" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_local_sandbox_execute_bounds_drain_wait(
    monkeypatch,
    caplog,
    tmp_path: Path,
) -> None:
    backend = LocalSandboxBackend()
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=tmp_path,
    )
    process = _HangingDrainProcess()
    observed_timeouts: list[int | float] = []

    async def fake_create_subprocess_shell(*args, **kwargs):
        return process

    async def fake_wait_for(awaitable, timeout):
        observed_timeouts.append(timeout)
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create_subprocess_shell)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    caplog.set_level(logging.WARNING, logger="codey.saas.sandbox.manager")

    result = await backend.execute(
        sandbox,
        "sleep 60",
        timeout=1,
        env=None,
    )

    assert result.timed_out is True
    assert process.killed is True
    assert observed_timeouts == [1, sandbox_manager._SANDBOX_DRAIN_TIMEOUT_SECONDS]
    assert "Failed to drain timed-out sandbox process sandbox" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_local_sandbox_execute_returns_timeout_when_kill_fails(
    monkeypatch,
    caplog,
    tmp_path: Path,
) -> None:
    backend = LocalSandboxBackend()
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=tmp_path,
    )
    process = _FailingKillProcess()

    async def fake_create_subprocess_shell(*args, **kwargs):
        return process

    async def fake_wait_for(awaitable, timeout):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create_subprocess_shell)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    caplog.set_level(logging.WARNING, logger="codey.saas.sandbox.manager")

    result = await backend.execute(
        sandbox,
        "sleep 60",
        timeout=1,
        env=None,
    )

    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.stderr == "Command timed out"
    assert process.kill_attempted is True
    assert process.communicate_calls == 0
    assert "secret" not in caplog.text
    assert "https://***@example.test/repo.git" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_local_sandbox_execute_redacts_credentials_from_startup_errors(
    caplog,
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = LocalSandboxBackend()
    sandbox_id = "https://sandbox-user:secret@example.test/id?token=sandbox-token"
    sandbox = Sandbox(
        id=sandbox_id,
        user_id="user",
        session_id="session",
        root=tmp_path / "sandbox",
        timeout=60,
    )

    async def fail_create_subprocess_shell(*args, **kwargs):
        raise RuntimeError(
            "spawn failed https://user:url-secret@example.test/repo.git"
            "?access_token=access-secret&client_secret=query-client-secret "
            "for operator@example.test api_key=api-secret "
            "auth_token=auth-secret refresh_token=refresh-secret "
            "password=inline-password authorization=Bearer bearer-secret",
        )

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_shell",
        fail_create_subprocess_shell,
    )
    caplog.set_level(logging.WARNING, logger="codey.saas.sandbox.manager")

    result = await backend.execute(
        sandbox,
        "echo ok",
        timeout=5,
        env=None,
    )

    assert result.exit_code == -1
    assert result.stdout == ""
    assert "url-secret" not in result.stderr
    assert "access-secret" not in result.stderr
    assert "query-client-secret" not in result.stderr
    assert "api-secret" not in result.stderr
    assert "auth-secret" not in result.stderr
    assert "refresh-secret" not in result.stderr
    assert "inline-password" not in result.stderr
    assert "bearer-secret" not in result.stderr
    assert "operator@example.test" not in result.stderr
    assert "https://***@example.test/repo.git?access_token=***&client_secret=***" in result.stderr
    assert "api_key=***" in result.stderr
    assert "auth_token=***" in result.stderr
    assert "refresh_token=***" in result.stderr
    assert "password=***" in result.stderr
    assert "authorization=Bearer ***" in result.stderr
    assert "***@example.test" in result.stderr
    assert "https://sandbox-user:secret@" not in caplog.text
    assert "url-secret" not in caplog.text
    assert "sandbox-token" not in caplog.text
    assert "access-secret" not in caplog.text
    assert "query-client-secret" not in caplog.text
    assert "api-secret" not in caplog.text
    assert "auth-secret" not in caplog.text
    assert "refresh-secret" not in caplog.text
    assert "inline-password" not in caplog.text
    assert "bearer-secret" not in caplog.text
    assert "operator@example.test" not in caplog.text
    assert "https://***@example.test/id?token=***" in caplog.text
    assert "https://***@example.test/repo.git?access_token=***&client_secret=***" in caplog.text


@pytest.mark.asyncio
async def test_e2b_sandbox_execute_redacts_credentials_from_startup_errors(
    caplog,
    tmp_path: Path,
) -> None:
    from codey.saas.sandbox.manager import E2BSandboxBackend

    class _FailingProcess:
        def start_and_wait(self, command, *, timeout, env_vars):
            raise RuntimeError(
                "spawn failed https://user:url-secret@example.test/repo.git"
                "?access_token=access-secret&client_secret=query-client-secret "
                "for operator@example.test api_key=api-secret "
                "auth_token=auth-secret refresh_token=refresh-secret "
                "password=inline-password authorization=Bearer bearer-secret",
            )

    sandbox_id = "https://sandbox-user:secret@example.test/id?token=sandbox-token"
    backend = E2BSandboxBackend.__new__(E2BSandboxBackend)
    backend._e2b_sandboxes = {
        sandbox_id: type("E2B", (), {"process": _FailingProcess()})()
    }
    sandbox = Sandbox(
        id=sandbox_id,
        user_id="user",
        session_id="session",
        root=tmp_path / "sandbox",
        timeout=60,
    )
    caplog.set_level(logging.WARNING, logger="codey.saas.sandbox.manager")

    result = await backend.execute(
        sandbox,
        "echo ok",
        timeout=5,
        env=None,
    )

    assert result.exit_code == -1
    assert result.stdout == ""
    assert "url-secret" not in result.stderr
    assert "access-secret" not in result.stderr
    assert "query-client-secret" not in result.stderr
    assert "api-secret" not in result.stderr
    assert "auth-secret" not in result.stderr
    assert "refresh-secret" not in result.stderr
    assert "inline-password" not in result.stderr
    assert "bearer-secret" not in result.stderr
    assert "operator@example.test" not in result.stderr
    assert "https://***@example.test/repo.git?access_token=***&client_secret=***" in result.stderr
    assert "api_key=***" in result.stderr
    assert "auth_token=***" in result.stderr
    assert "refresh_token=***" in result.stderr
    assert "password=***" in result.stderr
    assert "authorization=Bearer ***" in result.stderr
    assert "***@example.test" in result.stderr
    assert "https://sandbox-user:secret@" not in caplog.text
    assert "url-secret" not in caplog.text
    assert "sandbox-token" not in caplog.text
    assert "access-secret" not in caplog.text
    assert "query-client-secret" not in caplog.text
    assert "api-secret" not in caplog.text
    assert "auth-secret" not in caplog.text
    assert "refresh-secret" not in caplog.text
    assert "inline-password" not in caplog.text
    assert "bearer-secret" not in caplog.text
    assert "operator@example.test" not in caplog.text
    assert "https://***@example.test/id?token=***" in caplog.text
    assert "https://***@example.test/repo.git?access_token=***&client_secret=***" in caplog.text


@pytest.mark.asyncio
async def test_local_sandbox_execute_protects_sandbox_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = LocalSandboxBackend()
    root = tmp_path / "sandbox"
    (root / "workspace").mkdir(parents=True)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
    )
    captured_env: dict[str, str] = {}

    async def fake_create_subprocess_shell(*args, **kwargs):
        captured_env.update(kwargs["env"])
        return _CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create_subprocess_shell)

    result = await backend.execute(
        sandbox,
        "env",
        timeout=1,
        env={
            "HOME": "/outside",
            "LD_PRELOAD": "evil.so",
            "LD_LIBRARY_PATH": "/outside/lib",
            "CUSTOM_TOKEN": "kept",
            "BAD\nKEY": "dropped",
            "BAD_VALUE": "bad\x00value",
            "BAD_DEL": "bad\x7fvalue",
        },
    )

    assert result.exit_code == 0
    assert captured_env["HOME"] == str(root)
    assert captured_env["CUSTOM_TOKEN"] == "kept"
    assert "LD_PRELOAD" not in captured_env
    assert "LD_LIBRARY_PATH" not in captured_env
    assert "BAD\nKEY" not in captured_env
    assert "BAD_VALUE" not in captured_env
    assert "BAD_DEL" not in captured_env


def test_resolve_path_blocks_workspace_prefix_traversal(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (root / "workspace-evil").mkdir()

    manager = SandboxManager.__new__(SandboxManager)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
    )

    with pytest.raises(PermissionError):
        manager._resolve_path(sandbox, "../workspace-evil/escape.txt")


def test_resolve_path_redacts_traversal_error_path(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (root / "workspace-evil").mkdir()

    manager = SandboxManager.__new__(SandboxManager)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
    )

    with pytest.raises(PermissionError) as exc_info:
        manager._resolve_path(
            sandbox,
            "../workspace-evil/https://user:path-secret@example.test/repo.git"
            "?token=path-token",
        )

    message = str(exc_info.value)
    assert "path-secret" not in message
    assert "path-token" not in message
    assert "https://***@example.test/repo.git?token=***" in message


def test_get_redacts_missing_sandbox_id() -> None:
    manager = SandboxManager.__new__(SandboxManager)
    manager._sandboxes = {}

    with pytest.raises(ValueError) as exc_info:
        manager._get(
            "https://sandbox-user:sandbox-secret@example.test/id"
            "?token=sandbox-token"
        )

    message = str(exc_info.value)
    assert "sandbox-secret" not in message
    assert "sandbox-token" not in message
    assert "https://***@example.test/id?token=***" in message


def test_sandbox_root_for_id_rejects_path_traversal_ids() -> None:
    with pytest.raises(ValueError, match="Invalid sandbox id"):
        _sandbox_root_for_id("../outside")

    with pytest.raises(ValueError, match="Invalid sandbox id"):
        _sandbox_root_for_id("nested/sandbox")


@pytest.mark.asyncio
async def test_local_backend_create_rejects_path_traversal_sandbox_id(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sandbox_manager, "SANDBOX_ROOT", tmp_path / "sandboxes")

    with pytest.raises(ValueError, match="Invalid sandbox id"):
        await LocalSandboxBackend().create("../outside", "user", "session", 60)

    assert (tmp_path / "outside").exists() is False


@pytest.mark.asyncio
async def test_download_zip_skips_symlinked_files(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "safe.txt").write_text("safe", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (workspace / "secret-link.txt").symlink_to(secret)

    manager = SandboxManager.__new__(SandboxManager)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
    )
    manager._sandboxes = {"sandbox": sandbox}

    archive_bytes = await manager.download_zip("sandbox")

    archive_path = tmp_path / "sandbox.zip"
    archive_path.write_bytes(archive_bytes)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["safe.txt"]
        assert archive.read("safe.txt") == b"safe"


@pytest.mark.asyncio
async def test_download_zip_skips_files_that_disappear_during_export(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "sandbox"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "safe.txt").write_text("safe", encoding="utf-8")
    vanishing = workspace / "vanishing.txt"
    vanishing.write_text("gone", encoding="utf-8")

    original_stat = Path.stat
    def flaky_stat(path: Path, *args, **kwargs):
        if path.name == vanishing.name:
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    manager = SandboxManager.__new__(SandboxManager)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
    )
    manager._sandboxes = {"sandbox": sandbox}

    archive_bytes = await manager.download_zip("sandbox")

    archive_path = tmp_path / "sandbox.zip"
    archive_path.write_bytes(archive_bytes)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["safe.txt"]
        assert archive.read("safe.txt") == b"safe"


@pytest.mark.asyncio
async def test_download_zip_skips_files_that_become_symlinks_before_write(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "sandbox"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "safe.txt").write_text("safe", encoding="utf-8")
    race_file = workspace / "race.txt"
    race_file.write_text("race", encoding="utf-8")

    original_is_symlink = Path.is_symlink
    race_checks = 0

    def flaky_is_symlink(path: Path) -> bool:
        nonlocal race_checks
        if path == race_file:
            race_checks += 1
            return race_checks > 1
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", flaky_is_symlink)

    manager = SandboxManager.__new__(SandboxManager)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
    )
    manager._sandboxes = {"sandbox": sandbox}

    archive_bytes = await manager.download_zip("sandbox")

    archive_path = tmp_path / "sandbox.zip"
    archive_path.write_bytes(archive_bytes)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["safe.txt"]
        assert archive.read("safe.txt") == b"safe"


@pytest.mark.asyncio
async def test_download_zip_sanitizes_colliding_archive_names(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    workspace = root / "workspace"
    source_dir = workspace / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "bad\nname.py").write_text("one", encoding="utf-8")
    (source_dir / "bad_name.py").write_text("two", encoding="utf-8")

    manager = SandboxManager.__new__(SandboxManager)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
    )
    manager._sandboxes = {"sandbox": sandbox}

    archive_bytes = await manager.download_zip("sandbox")

    archive_path = tmp_path / "sandbox.zip"
    archive_path.write_bytes(archive_bytes)
    with zipfile.ZipFile(archive_path) as archive:
        assert sorted(archive.namelist()) == [
            "src/bad_name-2.py",
            "src/bad_name.py",
        ]
        assert {archive.read(name) for name in archive.namelist()} == {
            b"one",
            b"two",
        }


@pytest.mark.asyncio
async def test_download_zip_keeps_deduped_archive_names_bounded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "sandbox"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "one.txt").write_text("one", encoding="utf-8")
    (workspace / "two.txt").write_text("two", encoding="utf-8")
    max_archive_name = "x" * MAX_ARCHIVE_PATH_CHARS

    monkeypatch.setattr(
        sandbox_manager,
        "safe_archive_path",
        lambda path: max_archive_name,
    )

    manager = SandboxManager.__new__(SandboxManager)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
    )
    manager._sandboxes = {"sandbox": sandbox}

    archive_bytes = await manager.download_zip("sandbox")

    archive_path = tmp_path / "sandbox.zip"
    archive_path.write_bytes(archive_bytes)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()

    bounded_duplicates = [name for name in names if name != max_archive_name]
    assert max_archive_name in names
    assert len(bounded_duplicates) == 1
    assert len(bounded_duplicates[0]) <= MAX_ARCHIVE_PATH_CHARS


@pytest.mark.asyncio
async def test_list_files_skips_symlinked_files(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "safe.txt").write_text("safe", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (workspace / "secret-link.txt").symlink_to(secret)

    manager = SandboxManager.__new__(SandboxManager)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
    )
    manager._sandboxes = {"sandbox": sandbox}

    assert await manager.list_files("sandbox") == ["safe.txt"]
    assert await manager.list_files("sandbox", recursive=False) == ["safe.txt"]


@pytest.mark.asyncio
async def test_e2b_destroy_closes_sync_sandbox_and_removes_local_mirror(
    tmp_path: Path,
) -> None:
    from codey.saas.sandbox.manager import E2BSandboxBackend

    backend = E2BSandboxBackend.__new__(E2BSandboxBackend)
    closable = _SyncClosableSandbox()
    backend._e2b_sandboxes = {"sandbox": closable}

    root = tmp_path / "sandbox"
    (root / "workspace").mkdir(parents=True)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
    )

    await backend.destroy(sandbox)

    assert closable.closed is True
    assert "sandbox" not in backend._e2b_sandboxes
    assert root.exists() is False


@pytest.mark.asyncio
async def test_e2b_create_rejects_whitespace_api_key_before_construction(monkeypatch) -> None:
    from codey.saas.sandbox.manager import E2BSandboxBackend

    backend = E2BSandboxBackend.__new__(E2BSandboxBackend)
    backend._e2b_sandboxes = {}

    class _UnexpectedE2BSandbox:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("E2B sandbox should not be constructed")

    backend._E2BSandbox = _UnexpectedE2BSandbox
    monkeypatch.setenv("E2B_API_KEY", "   ")

    with pytest.raises(RuntimeError, match="E2B_API_KEY must be set"):
        await backend.create("sandbox", "user", "session", 60)

    assert backend._e2b_sandboxes == {}


@pytest.mark.asyncio
async def test_e2b_create_rejects_internal_whitespace_api_key_before_construction(
    monkeypatch,
) -> None:
    from codey.saas.sandbox.manager import E2BSandboxBackend

    backend = E2BSandboxBackend.__new__(E2BSandboxBackend)
    backend._e2b_sandboxes = {}

    class _UnexpectedE2BSandbox:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("E2B sandbox should not be constructed")

    backend._E2BSandbox = _UnexpectedE2BSandbox
    monkeypatch.setenv("E2B_API_KEY", "e2b bad")

    with pytest.raises(RuntimeError, match="E2B_API_KEY must be set"):
        await backend.create("sandbox", "user", "session", 60)

    assert backend._e2b_sandboxes == {}


@pytest.mark.asyncio
async def test_e2b_create_rejects_path_traversal_sandbox_id_before_construction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from codey.saas.sandbox.manager import E2BSandboxBackend

    backend = E2BSandboxBackend.__new__(E2BSandboxBackend)
    backend._e2b_sandboxes = {}

    class _UnexpectedE2BSandbox:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("E2B sandbox should not be constructed")

    backend._E2BSandbox = _UnexpectedE2BSandbox
    monkeypatch.setattr(sandbox_manager, "SANDBOX_ROOT", tmp_path / "sandboxes")
    monkeypatch.setenv("E2B_API_KEY", "e2b-ok")

    with pytest.raises(ValueError, match="Invalid sandbox id"):
        await backend.create("../outside", "user", "session", 60)

    assert backend._e2b_sandboxes == {}
    assert (tmp_path / "outside").exists() is False


@pytest.mark.asyncio
async def test_e2b_create_rejects_control_character_api_key_before_construction(
    monkeypatch,
) -> None:
    from codey.saas.sandbox.manager import E2BSandboxBackend

    backend = E2BSandboxBackend.__new__(E2BSandboxBackend)
    backend._e2b_sandboxes = {}

    class _UnexpectedE2BSandbox:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("E2B sandbox should not be constructed")

    backend._E2BSandbox = _UnexpectedE2BSandbox
    monkeypatch.setenv("E2B_API_KEY", "e2b\tbad")

    with pytest.raises(RuntimeError, match="E2B_API_KEY must be set"):
        await backend.create("sandbox", "user", "session", 60)

    assert backend._e2b_sandboxes == {}


@pytest.mark.asyncio
async def test_e2b_destroy_awaits_async_sandbox_close(tmp_path: Path) -> None:
    from codey.saas.sandbox.manager import E2BSandboxBackend

    backend = E2BSandboxBackend.__new__(E2BSandboxBackend)
    closable = _AsyncClosableSandbox()
    backend._e2b_sandboxes = {"sandbox": closable}

    root = tmp_path / "sandbox"
    (root / "workspace").mkdir(parents=True)
    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=root,
    )

    await backend.destroy(sandbox)

    assert closable.closed is True
    assert root.exists() is False


@pytest.mark.asyncio
async def test_manager_destroy_removes_stale_registry_entry_on_backend_error(
    tmp_path: Path,
) -> None:
    manager = SandboxManager.__new__(SandboxManager)
    manager._backend = _FailingBackend()

    sandbox = Sandbox(
        id="sandbox",
        user_id="user",
        session_id="session",
        root=tmp_path / "sandbox",
    )
    manager._sandboxes = {"sandbox": sandbox}

    with pytest.raises(RuntimeError, match="backend destroy failed"):
        await manager.destroy("sandbox")

    assert manager.get_sandbox("sandbox") is None


@pytest.mark.asyncio
async def test_cleanup_expired_continues_after_backend_destroy_error(
    caplog,
    tmp_path: Path,
) -> None:
    manager = SandboxManager.__new__(SandboxManager)
    manager._backend = _FailingBackend()
    manager._sandboxes = {
        "sandbox-1": Sandbox(
            id="sandbox-1",
            user_id="user",
            session_id="session",
            root=tmp_path / "sandbox-1",
            created_at=0,
        ),
        "sandbox-2": Sandbox(
            id="sandbox-2",
            user_id="user",
            session_id="session",
            root=tmp_path / "sandbox-2",
            created_at=0,
        ),
    }
    caplog.set_level(logging.WARNING, logger="codey.saas.sandbox.manager")

    removed = await manager.cleanup_expired(max_age_seconds=1)

    assert removed == 2
    assert manager._sandboxes == {}
    assert "secret" not in caplog.text
    assert "https://***@example.test/repo.git" in caplog.text
    assert "Traceback" not in caplog.text
