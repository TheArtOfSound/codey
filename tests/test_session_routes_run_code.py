from __future__ import annotations

import asyncio
import base64
import logging
import re
import sys
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

import codey.saas.api.session_routes as session_routes


class _SuccessfulProcess:
    def __init__(self) -> None:
        self.returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"ok\n", b""


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
        self.drain_attempted = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.killed:
            self.drain_attempted = True
            raise RuntimeError("drain failed https://user:secret@example.test/repo")
        await asyncio.sleep(3600)
        return b"", b""

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
        self.drain_attempted = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.drain_attempted = True
        await asyncio.sleep(3600)
        return b"", b""

    def kill(self) -> None:
        self.kill_attempted = True
        raise PermissionError("cannot kill https://user:secret@example.test/repo")


class _ModuleMissingProcess:
    def __init__(self) -> None:
        self.returncode = 1

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b"ModuleNotFoundError: No module named 'yaml'"


class _FailedInstallProcess:
    def __init__(self) -> None:
        self.returncode = 1

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b"ERROR: https://user:secret@example.test/simple missing"


class _StdoutOnlyErrorProcess:
    def __init__(self) -> None:
        self.returncode = 1

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"Traceback: boom", b""


class _SyntaxErrorProcess:
    def __init__(self) -> None:
        self.returncode = 1

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b"SyntaxError: invalid syntax"


class _E2BResult:
    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class _E2BCommands:
    def __init__(self, result: _E2BResult) -> None:
        self._result = result

    def run(self, *_args, **_kwargs) -> _E2BResult:
        return self._result


class _CapturingE2BCommands:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command: str, *_args, **_kwargs) -> _E2BResult:
        self.commands.append(command)
        return _E2BResult(stdout="remote ok\n")


class _KillFailingSandbox:
    def __init__(self, *args, **kwargs) -> None:
        self.commands = _E2BCommands(_E2BResult(stdout="remote ok\n"))

    def kill(self) -> None:
        raise RuntimeError("sandbox already gone")


class _PartialE2BResultSandbox:
    def __init__(self, *args, **kwargs) -> None:
        self.commands = _E2BCommands(types.SimpleNamespace(stdout="remote ok\n"))

    def kill(self) -> None:
        return None


class _CapturingE2BSandbox:
    last: "_CapturingE2BSandbox | None" = None

    def __init__(self, *args, **kwargs) -> None:
        type(self).last = self
        self.commands = _CapturingE2BCommands()

    def kill(self) -> None:
        return None


def test_run_code_request_rejects_blank_code() -> None:
    with pytest.raises(ValidationError):
        session_routes.RunCodeRequest(code="   ")


def test_run_code_request_preserves_non_blank_code() -> None:
    code = "\nprint('ok')\n"

    request = session_routes.RunCodeRequest(code=code)

    assert request.code == code


def test_run_code_request_rejects_oversized_code() -> None:
    with pytest.raises(ValidationError):
        session_routes.RunCodeRequest(
            code="x" * (session_routes._MAX_RUN_CODE_CHARS + 1),
        )


def test_run_code_request_normalizes_blank_language_to_python() -> None:
    request = session_routes.RunCodeRequest(code="print('ok')", language="   ")

    assert request.language == "python"


def test_run_code_request_normalizes_language_aliases() -> None:
    request = session_routes.RunCodeRequest(code="console.log('ok')", language="  JS  ")

    assert request.language == "javascript"


def test_run_code_request_rejects_unsupported_language() -> None:
    with pytest.raises(ValidationError):
        session_routes.RunCodeRequest(code="puts 'ok'", language="ruby")


def test_coerce_run_code_fix_text_falls_back_across_mapping_fields() -> None:
    result = session_routes._coerce_run_code_fix_text(
        {
            "content": [],
            "text": "```python\nprint('fixed')\n```",
        }
    )

    assert result == "```python\nprint('fixed')\n```"


def test_redact_session_error_hides_common_secret_shapes() -> None:
    message = session_routes._redact_session_error(
        RuntimeError(
            "failed https://user:url-secret@example.test/repo"
            "?access_token=query-secret authorization=Bearer bearer-secret "
            "mirror=https://example.test/repo#client_secret=fragment-secret "
            "for operator@example.test"
        )
    )

    assert "url-secret" not in message
    assert "query-secret" not in message
    assert "fragment-secret" not in message
    assert "bearer-secret" not in message
    assert "operator@example.test" not in message
    assert "https://***@example.test/repo" in message
    assert "access_token=***" in message
    assert "client_secret=***" in message
    assert "authorization=Bearer ***" in message
    assert "[redacted-email]" in message


def test_session_runtime_normalizers_reject_internal_whitespace() -> None:
    assert session_routes._coerce_session_github_token(" gh-token ") == "gh-token"
    assert session_routes._coerce_session_github_token("gh-token bad") is None
    assert session_routes._coerce_session_clone_url(
        " https://github.com/example/repo.git "
    ) == "https://github.com/example/repo.git"
    assert session_routes._coerce_session_clone_url(
        "https://github.com/example/repo.git bad"
    ) is None
    assert session_routes._coerce_session_runtime_secret(" e2b-key ") == "e2b-key"
    assert session_routes._coerce_session_runtime_secret("e2b-key bad") is None


def test_coerce_prompt_output_text_accepts_structured_text_blocks() -> None:
    result = session_routes._coerce_prompt_output_text(
        {
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
                {"type": "image", "source": "ignored"},
            ]
        }
    )

    assert result == "first\nsecond"


def test_coerce_prompt_output_text_rejects_blank_output() -> None:
    for value in (" \n\t", {"content": "   "}):
        with pytest.raises(TypeError, match="empty prompt output"):
            session_routes._coerce_prompt_output_text(value)


@pytest.mark.asyncio
async def test_run_code_cleans_up_temp_dir_on_success(monkeypatch, tmp_path: Path) -> None:
    run_dir = tmp_path / "codey_run_success"

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _SuccessfulProcess()

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('ok')"),
        current_user=object(),
    )

    assert response.stdout == "ok\n"
    assert response.exit_code == 0
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_cleans_up_temp_dir_when_initial_write_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_write_failure"

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    original_write_text = Path.write_text

    def fail_initial_write(path: Path, *args, **kwargs):
        if path == run_dir / "main.py":
            raise OSError("write failed https://user:secret@example.test/repo")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(Path, "write_text", fail_initial_write)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('ok')"),
        current_user=object(),
    )

    assert response.exit_code == -1
    assert response.timed_out is False
    assert "secret" not in response.stderr
    assert "https://***@example.test/repo" in response.stderr
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_kills_timed_out_process_and_cleans_temp_dir(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_timeout"
    process = _HangingProcess()

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    async def fake_wait_for(awaitable, timeout):
        if process.killed:
            return await awaitable
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('timeout')"),
        current_user=object(),
    )

    assert response.timed_out is True
    assert process.killed is True
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_tolerates_kill_race_on_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_timeout_race"
    process = _RaceExitedProcess()

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

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

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('timeout')"),
        current_user=object(),
    )

    assert response.timed_out is True
    assert response.stderr == "Execution timed out (30s limit)"
    assert process.communicate_calls == 1
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_terminate_subprocess_tolerates_drain_failure(caplog) -> None:
    process = _FailingDrainProcess()
    caplog.set_level(logging.WARNING, logger="codey.saas.api.session_routes")

    await session_routes._terminate_subprocess(process)

    assert process.killed is True
    assert process.drain_attempted is True
    assert "secret" not in caplog.text
    assert "https://***@example.test/repo" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_terminate_subprocess_bounds_drain_wait(caplog, monkeypatch) -> None:
    process = _HangingDrainProcess()
    observed_timeout: float | None = None
    caplog.set_level(logging.WARNING, logger="codey.saas.api.session_routes")

    async def fake_wait_for(awaitable, timeout):
        nonlocal observed_timeout
        observed_timeout = timeout
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    await session_routes._terminate_subprocess(process)

    assert process.killed is True
    assert observed_timeout == session_routes._RUN_CODE_DRAIN_TIMEOUT_SECONDS
    assert "Failed to drain timed-out run-code process" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_terminate_subprocess_tolerates_kill_failure(caplog) -> None:
    process = _FailingKillProcess()
    caplog.set_level(logging.WARNING, logger="codey.saas.api.session_routes")

    await session_routes._terminate_subprocess(process)

    assert process.kill_attempted is True
    assert process.drain_attempted is False
    assert "secret" not in caplog.text
    assert "https://***@example.test/repo" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_run_code_installs_missing_module_with_same_python_runner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_retry"
    commands: list[tuple[str, ...]] = []
    processes = iter([
        _ModuleMissingProcess(),
        _SuccessfulProcess(),
        _SuccessfulProcess(),
    ])

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        commands.append(tuple(args))
        return next(processes)

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="import yaml\nprint('ok')"),
        current_user=object(),
    )

    assert response.stdout == "ok\n"
    assert commands[0][0] == sys.executable
    assert commands[1][:5] == (sys.executable, "-m", "pip", "install", "-q")
    assert commands[1][5] == "pyyaml"
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_reports_failed_missing_module_install(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_failed_install"
    commands: list[tuple[str, ...]] = []
    processes = iter([
        _ModuleMissingProcess(),
        _FailedInstallProcess(),
    ])

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        commands.append(tuple(args))
        return next(processes)

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="import yaml\nprint('ok')"),
        current_user=object(),
    )

    assert response.stdout == ""
    assert response.exit_code == 1
    assert response.timed_out is False
    assert response.stderr == (
        "Dependency install failed: ERROR: https://***@example.test/simple missing"
    )
    assert "secret" not in response.stderr
    assert commands[1][:5] == (sys.executable, "-m", "pip", "install", "-q")
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_does_not_pip_install_for_javascript_module_text(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_js_module_text"
    commands: list[tuple[str, ...]] = []

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        commands.append(tuple(args))
        return _ModuleMissingProcess()

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(
            code="console.error(\"ModuleNotFoundError: No module named 'yaml'\"); process.exit(1)",
            language="javascript",
        ),
        current_user=object(),
    )

    assert len(commands) == 1
    assert commands[0][0] == "node"
    assert response.exit_code == 1
    assert "ModuleNotFoundError: No module named 'yaml'" in response.stderr
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_falls_back_to_stdout_when_stderr_is_empty(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_stdout_error"

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _StdoutOnlyErrorProcess()

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="raise SystemExit(1)"),
        current_user=object(),
    )

    assert response.exit_code == 1
    assert response.stdout == "Traceback: boom"
    assert response.stderr == "Traceback: boom"
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_reports_missing_local_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_missing_runtime"

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "node")

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="console.log('ok')", language="javascript"),
        current_user=object(),
    )

    assert response.exit_code == -1
    assert response.timed_out is False
    assert response.stderr == "Runtime not available: node"
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_reports_local_runtime_startup_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_startup_failure"

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise OSError("spawn failed https://user:secret@example.test/repo")

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('ok')"),
        current_user=object(),
    )

    assert response.exit_code == -1
    assert response.timed_out is False
    assert response.stderr.startswith(f"Runtime startup failed for {sys.executable}: ")
    assert "secret" not in response.stderr
    assert "https://***@example.test/repo" in response.stderr
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_redacts_credentials_from_internal_execution_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_internal_error"

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise RuntimeError("spawn failed https://user:secret@example.test/repo")

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('ok')"),
        current_user=object(),
    )

    assert response.exit_code == -1
    assert response.timed_out is False
    assert "secret" not in response.stderr
    assert "https://***@example.test/repo" in response.stderr
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_auto_fix_accepts_mapping_model_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_syntax_fix"
    commands: list[tuple[str, ...]] = []
    fix_prompts: list[str] = []
    write_encodings: list[str | None] = []
    processes = iter([
        _SyntaxErrorProcess(),
        _SyntaxErrorProcess(),
        _SuccessfulProcess(),
    ])
    fixes = iter([
        "```python\nprint('first fix ✓')\n```",
        "```python\nprint('second fix ✓')\n```",
    ])

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        commands.append(tuple(args))
        return next(processes)

    async def fake_call_model(provider, model, messages, **kwargs):
        fix_prompts.append(messages[1]["content"])
        return {"content": next(fixes)}

    original_write_text = session_routes.Path.write_text

    def spy_write_text(self, data, *args, **kwargs):
        if self.parent == run_dir:
            write_encodings.append(kwargs.get("encoding"))
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(session_routes.Path, "write_text", spy_write_text)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fake_call_model,
    )

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('broken'"),
        current_user=object(),
    )

    assert response.stdout == "ok\n"
    assert response.exit_code == 0
    assert len(commands) == 3
    assert "print('broken'" in fix_prompts[0]
    assert "print('first fix ✓')" in fix_prompts[1]
    assert write_encodings == ["utf-8", "utf-8", "utf-8"]
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_auto_fix_rejects_blank_model_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_blank_syntax_fix"
    commands: list[tuple[str, ...]] = []

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        commands.append(tuple(args))
        return _SyntaxErrorProcess()

    async def fake_call_model(*args, **kwargs):
        return {"content": "   "}

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fake_call_model,
    )

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('broken'"),
        current_user=object(),
    )

    assert response.stdout == ""
    assert response.stderr == "SyntaxError: invalid syntax"
    assert response.exit_code == 1
    assert len(commands) == 1
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_does_not_python_autofix_javascript_syntax_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_js_syntax"
    commands: list[tuple[str, ...]] = []
    call_model_called = False

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        commands.append(tuple(args))
        return _SyntaxErrorProcess()

    async def fake_call_model(*args, **kwargs):
        nonlocal call_model_called
        call_model_called = True
        return {"content": "```python\nprint('should not run')\n```"}

    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fake_call_model,
    )

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="console.log(", language="javascript"),
        current_user=object(),
    )

    assert response.exit_code == 1
    assert response.stderr == "SyntaxError: invalid syntax"
    assert len(commands) == 1
    assert call_model_called is False
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_preserves_successful_e2b_result_when_kill_fails(
    monkeypatch,
) -> None:
    fake_module = types.SimpleNamespace(Sandbox=_KillFailingSandbox)

    async def fail_if_local_exec(*args, **kwargs):
        raise AssertionError("local subprocess fallback should not run")

    monkeypatch.setenv("E2B_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", fake_module)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_if_local_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('remote')"),
        current_user=object(),
    )

    assert response.stdout == "remote ok\n"
    assert response.stderr == ""
    assert response.exit_code == 0
    assert response.timed_out is False


@pytest.mark.asyncio
async def test_run_code_preserves_partial_successful_e2b_result(
    monkeypatch,
) -> None:
    fake_module = types.SimpleNamespace(Sandbox=_PartialE2BResultSandbox)

    async def fail_if_local_exec(*args, **kwargs):
        raise AssertionError("local subprocess fallback should not run")

    monkeypatch.setenv("E2B_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", fake_module)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_if_local_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('remote')"),
        current_user=object(),
    )

    assert response.stdout == "remote ok\n"
    assert response.stderr == ""
    assert response.exit_code == 0
    assert response.timed_out is False


@pytest.mark.asyncio
async def test_run_code_e2b_executes_base64_encoded_python(
    monkeypatch,
) -> None:
    fake_module = types.SimpleNamespace(Sandbox=_CapturingE2BSandbox)
    code = "print(\"safe ''' payload\")"

    async def fail_if_local_exec(*args, **kwargs):
        raise AssertionError("local subprocess fallback should not run")

    monkeypatch.setenv("E2B_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", fake_module)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_if_local_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code=code),
        current_user=object(),
    )

    sandbox = _CapturingE2BSandbox.last
    assert sandbox is not None
    command = sandbox.commands.commands[-1]
    match = re.search(r"b64decode\('([^']+)'\)", command)

    assert response.stdout == "remote ok\n"
    assert match is not None
    assert base64.b64decode(match.group(1)).decode("utf-8") == code
    assert code not in command
    assert "'''" not in command


@pytest.mark.asyncio
async def test_run_code_ignores_whitespace_e2b_key_and_uses_local_execution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_local_whitespace_e2b"

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _SuccessfulProcess()

    class _UnexpectedSandbox:
        constructed = False

        def __init__(self, *args, **kwargs) -> None:
            type(self).constructed = True
            raise AssertionError("E2B sandbox should not be constructed")

    monkeypatch.setenv("E2B_API_KEY", "   ")
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", types.SimpleNamespace(Sandbox=_UnexpectedSandbox))
    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('ok')"),
        current_user=object(),
    )

    assert response.stdout == "ok\n"
    assert response.exit_code == 0
    assert _UnexpectedSandbox.constructed is False
    assert run_dir.exists() is False


@pytest.mark.asyncio
async def test_run_code_ignores_control_character_e2b_key_and_uses_local_execution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "codey_run_local_control_e2b"

    def fake_mkdtemp(*args, **kwargs):
        run_dir.mkdir()
        return str(run_dir)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _SuccessfulProcess()

    class _UnexpectedSandbox:
        constructed = False

        def __init__(self, *args, **kwargs) -> None:
            type(self).constructed = True
            raise AssertionError("E2B sandbox should not be constructed")

    monkeypatch.setenv("E2B_API_KEY", "test\tkey")
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", types.SimpleNamespace(Sandbox=_UnexpectedSandbox))
    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await session_routes.run_code(
        session_routes.RunCodeRequest(code="print('ok')"),
        current_user=object(),
    )

    assert response.stdout == "ok\n"
    assert response.exit_code == 0
    assert _UnexpectedSandbox.constructed is False
    assert run_dir.exists() is False
