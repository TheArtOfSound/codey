from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import codey.saas.build_mode.validator as validator_module
from codey.saas.build_mode.validator import FileValidator, _redact_validation_error


class _FakeYamlError(Exception):
    pass


def test_redact_validation_error_removes_common_secret_shapes() -> None:
    message = _redact_validation_error(
        "clone failed https://runner:secret@example.test/repo.git?access_token=access123&client_secret=client123 "
        "for operator@example.test api_key=key123 auth_token=auth123 "
        "refresh_token=refresh123 password=pw123 authorization=Bearer bearer123"
    )

    assert "runner:secret" not in message
    assert "secret@example.test" not in message
    assert "access123" not in message
    assert "client123" not in message
    assert "key123" not in message
    assert "auth123" not in message
    assert "refresh123" not in message
    assert "pw123" not in message
    assert "bearer123" not in message
    assert "operator@example.test" not in message
    assert "https://***@example.test/repo.git?access_token=***&client_secret=***" in message
    assert "api_key=***" in message
    assert "auth_token=***" in message
    assert "refresh_token=***" in message
    assert "password=***" in message
    assert "authorization=Bearer ***" in message
    assert "***@example.test" in message


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


class _CompletedProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


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
            raise RuntimeError(
                "drain failed https://runner:secret@example.test/repo.git"
            )
        await asyncio.sleep(3600)
        return b"", b""

    def kill(self) -> None:
        self.killed = True


class _HangingDrainProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.drain_attempted = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.killed:
            self.drain_attempted = True
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
        raise PermissionError(
            "cannot kill https://runner:secret@example.test/repo.git"
        )


def test_python_import_validation_checks_relative_from_imports() -> None:
    validator = FileValidator()

    errors = validator.validate_imports(
        "from .utils import helper\nfrom .missing import value\n",
        "pkg/service.py",
        {"pkg/service.py", "pkg/utils.py"},
    )

    assert errors == [
        "pkg/service.py:2: Cannot resolve relative import '.missing'",
    ]


def test_python_import_validation_checks_relative_import_aliases() -> None:
    validator = FileValidator()

    errors = validator.validate_imports(
        "from . import utils, missing\n",
        "pkg/service.py",
        {"pkg/service.py", "pkg/utils.py"},
    )

    assert errors == [
        "pkg/service.py:1: Cannot resolve relative import '.missing'",
    ]


def test_python_import_validation_resolves_src_layout_absolute_imports() -> None:
    validator = FileValidator()

    errors = validator.validate_imports(
        "from app.utils import helper\nfrom app.missing import value\n",
        "src/app/main.py",
        {"src/app/main.py", "src/app/utils.py"},
    )

    assert errors == [
        "src/app/main.py:2: Cannot resolve 'from app.missing import ...'",
    ]


@pytest.mark.asyncio
async def test_validate_phase_prunes_dependency_dirs_without_rglob(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules" / "bad").mkdir(parents=True)
    (tmp_path / "node_modules" / "bad" / "broken.js").write_text(
        "function {",
        encoding="utf-8",
    )

    def fail_rglob(_self, _pattern):
        raise AssertionError("validate_phase should prune with os.walk")

    monkeypatch.setattr(Path, "rglob", fail_rglob)

    result = await validator.validate_phase(str(tmp_path), phase=1)

    assert result == {
        "tests_passed": 0,
        "tests_failed": 0,
        "import_errors": [],
        "lint_errors": [],
        "syntax_errors": [],
    }


@pytest.mark.asyncio
async def test_validate_phase_skips_oversized_files_but_preserves_import_resolution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    (package_dir / "main.py").write_text("from . import utils\n", encoding="utf-8")
    (package_dir / "utils.py").write_text(
        "x = '" + ("a" * 64) + "'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(validator_module, "_MAX_VALIDATION_FILE_CHARS", 32)

    result = await validator.validate_phase(str(tmp_path), phase=1)

    assert result["import_errors"] == []
    assert result["syntax_errors"] == []
    assert result["lint_errors"] == [
        "pkg/utils.py: skipped validation because file exceeds 32 characters",
    ]


@pytest.mark.asyncio
async def test_run_pytest_uses_current_python_interpreter(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()
    captured_args: tuple | None = None

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal captured_args
        captured_args = args
        return _CompletedProcess(stdout=b"1 passed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await validator._run_pytest(str(tmp_path))

    assert result["passed"] == 1
    assert captured_args is not None
    assert captured_args[:3] == (sys.executable, "-m", "pytest")


@pytest.mark.asyncio
async def test_run_pytest_kills_process_on_timeout(monkeypatch, tmp_path: Path) -> None:
    validator = FileValidator()
    process = _HangingProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    async def fake_wait_for(awaitable, timeout):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    result = await validator._run_pytest(str(tmp_path))

    assert result["passed"] == 0
    assert result["failed"] == 0
    assert result["errors"] == ["pytest timed out after 60s"]
    assert process.killed is True


@pytest.mark.asyncio
async def test_run_pytest_reports_stdout_when_nonstandard_failure_has_no_stderr(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()
    process = _CompletedProcess(
        returncode=2,
        stdout=b"usage: pytest [options]",
        stderr=b"",
    )

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await validator._run_pytest(str(tmp_path))

    assert result["passed"] == 0
    assert result["failed"] == 0
    assert result["errors"] == ["usage: pytest [options]"]


@pytest.mark.asyncio
async def test_run_pytest_reports_exit_one_without_failure_summary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()
    process = _CompletedProcess(
        returncode=1,
        stdout=b"",
        stderr=b"/usr/bin/python: No module named pytest",
    )

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await validator._run_pytest(str(tmp_path))

    assert result["passed"] == 0
    assert result["failed"] == 0
    assert result["errors"] == ["/usr/bin/python: No module named pytest"]


@pytest.mark.asyncio
async def test_run_pytest_reports_startup_os_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await validator._run_pytest(str(tmp_path))

    assert result["passed"] == 0
    assert result["failed"] == 0
    assert result["errors"] == ["failed to start pytest: exec format error"]


@pytest.mark.asyncio
async def test_run_pytest_redacts_credentials_from_startup_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise OSError("clone failed https://runner:token@example.test/repo.git")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await validator._run_pytest(str(tmp_path))

    assert result["errors"] == [
        "failed to start pytest: clone failed https://***@example.test/repo.git",
    ]


@pytest.mark.asyncio
async def test_run_pytest_redacts_credentials_from_fallback_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()
    process = _CompletedProcess(
        returncode=2,
        stdout=b"",
        stderr=b"fatal: https://runner:token@example.test/repo.git failed",
    )

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await validator._run_pytest(str(tmp_path))

    assert result["errors"] == [
        "fatal: https://***@example.test/repo.git failed",
    ]


@pytest.mark.asyncio
async def test_kill_timed_out_process_tolerates_process_lookup_race() -> None:
    validator = FileValidator()
    process = _RaceExitedProcess()

    await validator._kill_timed_out_process(process)

    assert process.communicated is True


@pytest.mark.asyncio
async def test_kill_timed_out_process_tolerates_drain_failure(caplog) -> None:
    validator = FileValidator()
    process = _FailingDrainProcess()
    caplog.set_level(logging.WARNING, logger="codey.saas.build_mode.validator")

    await validator._kill_timed_out_process(process)

    assert process.killed is True
    assert process.drain_attempted is True
    assert "secret" not in caplog.text
    assert "https://***@example.test/repo.git" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_kill_timed_out_process_bounds_drain_wait(
    caplog,
    monkeypatch,
) -> None:
    validator = FileValidator()
    process = _HangingDrainProcess()
    observed_timeout: float | None = None
    caplog.set_level(logging.WARNING, logger="codey.saas.build_mode.validator")

    async def fake_wait_for(awaitable, timeout):
        nonlocal observed_timeout
        observed_timeout = timeout
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    await validator._kill_timed_out_process(process)

    assert process.killed is True
    assert observed_timeout == validator_module._VALIDATION_DRAIN_TIMEOUT_SECONDS
    assert "Failed to drain timed-out validation process" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_kill_timed_out_process_tolerates_kill_failure(caplog) -> None:
    validator = FileValidator()
    process = _FailingKillProcess()
    caplog.set_level(logging.WARNING, logger="codey.saas.build_mode.validator")

    await validator._kill_timed_out_process(process)

    assert process.kill_attempted is True
    assert process.drain_attempted is False
    assert "secret" not in caplog.text
    assert "https://***@example.test/repo.git" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_run_tsc_kills_process_on_timeout(monkeypatch, tmp_path: Path) -> None:
    validator = FileValidator()
    process = _HangingProcess()
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    async def fake_wait_for(awaitable, timeout):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    result = await validator._run_tsc(str(tmp_path))

    assert result == ["tsc timed out after 90s"]
    assert process.killed is True


@pytest.mark.asyncio
async def test_run_tsc_disables_npx_auto_install(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()
    captured_args: tuple | None = None
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal captured_args
        captured_args = args
        return _CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await validator._run_tsc(str(tmp_path))

    assert result == []
    assert captured_args is not None
    assert captured_args[:3] == ("npx", "--no-install", "tsc")


@pytest.mark.asyncio
async def test_run_tsc_reports_stderr_when_no_ts_errors_are_present(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()
    process = _CompletedProcess(
        returncode=2,
        stdout=b"",
        stderr=b"npm exec could not determine executable to run",
    )
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await validator._run_tsc(str(tmp_path))

    assert result == ["npm exec could not determine executable to run"]


@pytest.mark.asyncio
async def test_run_tsc_reports_startup_os_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await validator._run_tsc(str(tmp_path))

    assert result == ["failed to start tsc: exec format error"]


@pytest.mark.asyncio
async def test_run_tsc_redacts_credentials_from_startup_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise OSError("clone failed https://runner:token@example.test/repo.git")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await validator._run_tsc(str(tmp_path))

    assert result == [
        "failed to start tsc: clone failed https://***@example.test/repo.git",
    ]


@pytest.mark.asyncio
async def test_run_tsc_redacts_credentials_from_fallback_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validator = FileValidator()
    process = _CompletedProcess(
        returncode=2,
        stdout=b"",
        stderr=b"fatal: https://runner:token@example.test/repo.git failed",
    )
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await validator._run_tsc(str(tmp_path))

    assert result == ["fatal: https://***@example.test/repo.git failed"]


@pytest.mark.asyncio
async def test_validate_phase_checks_yaml_syntax(monkeypatch, tmp_path: Path) -> None:
    def fake_safe_load(_content):
        raise _FakeYamlError("bad yaml")

    monkeypatch.setitem(
        sys.modules,
        "yaml",
        SimpleNamespace(safe_load=fake_safe_load, YAMLError=_FakeYamlError),
    )
    (tmp_path / "config.yaml").write_text("name: [broken\n", encoding="utf-8")

    result = await FileValidator().validate_phase(str(tmp_path), 1)

    assert result["syntax_errors"] == ["config.yaml: YAML error: bad yaml"]


@pytest.mark.asyncio
async def test_validate_phase_skips_symlinked_files_outside_project(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside.py"
    outside.write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "leak.py").symlink_to(outside)

    result = await FileValidator().validate_phase(str(tmp_path), 1)

    assert result["syntax_errors"] == []


@pytest.mark.asyncio
async def test_validate_phase_checks_mjs_syntax(tmp_path: Path) -> None:
    (tmp_path / "index.mjs").write_text("export function broken() {\n", encoding="utf-8")

    result = await FileValidator().validate_phase(str(tmp_path), 1)

    assert result["syntax_errors"] == ["index.mjs: Unbalanced braces (count: +1)"]


def test_js_import_resolution_includes_mjs_modules() -> None:
    validator = FileValidator()

    assert validator.validate_imports(
        "import { value } from './util';\nimport { nested } from './pkg';\n",
        "index.mjs",
        {"index.mjs", "util.mjs", "pkg/index.mjs"},
    ) == []


def test_js_import_validation_checks_side_effect_imports() -> None:
    validator = FileValidator()

    errors = validator.validate_imports(
        "import './missing';\nimport '../shared/setup';\n",
        "src/index.js",
        {"src/index.js", "shared/setup.js"},
    )

    assert errors == ["src/index.js:1: Cannot resolve import './missing'"]


def test_js_import_validation_checks_dynamic_imports() -> None:
    validator = FileValidator()

    errors = validator.validate_imports(
        "const page = await import('./pages/home');\nawait import('./pages/missing');\n",
        "src/router.js",
        {"src/router.js", "src/pages/home.js"},
    )

    assert errors == ["src/router.js:2: Cannot resolve import './pages/missing'"]


def test_js_import_validation_checks_re_export_specifiers() -> None:
    validator = FileValidator()

    errors = validator.validate_imports(
        "export { Button } from './components/button';\nexport * from './missing';\n",
        "src/index.ts",
        {"src/index.ts", "src/components/button.tsx"},
    )

    assert errors == ["src/index.ts:2: Cannot resolve import './missing'"]


def test_js_import_resolution_rejects_above_root_traversal() -> None:
    validator = FileValidator()

    errors = validator.validate_imports(
        "import secret from '../../secret';\n",
        "src/index.js",
        {"src/index.js", "secret.js"},
    )

    assert errors == ["src/index.js:1: Cannot resolve import '../../secret'"]
