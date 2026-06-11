from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import subprocess

import pytest

import codey.nfet.repository_loader as repository_loader


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
            raise RuntimeError(
                "drain failed https://x-access-token:secret@example.test/repo.git"
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
            "cannot kill https://x-access-token:secret@example.test/repo.git"
        )


class _FailedProcess:
    returncode = 128

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b"fatal: bad byte \xff"


class _StdoutOnlyFailedProcess:
    returncode = 128

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"fatal: repository not found", b""


class _SuccessfulProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


@pytest.mark.asyncio
async def test_clone_repository_async_times_out_and_kills_process(monkeypatch) -> None:
    process = _HangingProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(
        repository_loader.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(repository_loader, "CLONE_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(RuntimeError, match="timed out after 0.01s"):
        await repository_loader._clone_repository_async(
            "https://github.com/example/repo.git",
            Path("/tmp/repo"),
        )

    assert process.killed is True


@pytest.mark.asyncio
async def test_clone_repository_async_tolerates_kill_race_on_timeout(monkeypatch) -> None:
    process = _RaceExitedProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(
        repository_loader.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(repository_loader, "CLONE_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(RuntimeError, match="timed out after 0.01s"):
        await repository_loader._clone_repository_async(
            "https://github.com/example/repo.git",
            Path("/tmp/repo"),
        )

    assert process.communicated is True


@pytest.mark.asyncio
async def test_clone_repository_async_preserves_timeout_when_drain_fails(
    monkeypatch,
    caplog,
) -> None:
    process = _FailingDrainProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    wait_for_calls = 0

    async def fake_wait_for(awaitable, timeout):
        nonlocal wait_for_calls
        wait_for_calls += 1
        if wait_for_calls == 1:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise asyncio.TimeoutError()
        return await awaitable

    monkeypatch.setattr(
        repository_loader.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(repository_loader.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(repository_loader, "CLONE_TIMEOUT_SECONDS", 0.01)
    caplog.set_level(logging.WARNING, logger="codey.nfet.repository_loader")

    with pytest.raises(RuntimeError, match="timed out after 0.01s"):
        await repository_loader._clone_repository_async(
            "https://github.com/example/repo.git",
            Path("/tmp/repo"),
        )

    assert process.killed is True
    assert process.drain_attempted is True
    assert "secret" not in caplog.text
    assert "https://***@example.test/repo.git" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_terminate_timed_out_clone_bounds_drain_wait(monkeypatch) -> None:
    process = _HangingDrainProcess()
    monkeypatch.setattr(repository_loader, "CLONE_DRAIN_TIMEOUT_SECONDS", 0.01)

    await repository_loader._terminate_timed_out_clone(process)

    assert process.killed is True
    assert process.drain_attempted is True


@pytest.mark.asyncio
async def test_clone_repository_async_preserves_timeout_when_kill_fails(
    monkeypatch,
    caplog,
) -> None:
    process = _FailingKillProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    async def fake_wait_for(awaitable, timeout):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(
        repository_loader.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(repository_loader.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(repository_loader, "CLONE_TIMEOUT_SECONDS", 0.01)
    caplog.set_level(logging.WARNING, logger="codey.nfet.repository_loader")

    with pytest.raises(RuntimeError, match="timed out after 0.01s"):
        await repository_loader._clone_repository_async(
            "https://github.com/example/repo.git",
            Path("/tmp/repo"),
        )

    assert process.kill_attempted is True
    assert process.drain_attempted is False
    assert "secret" not in caplog.text
    assert "https://***@example.test/repo.git" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_clone_repository_async_normalizes_missing_git(monkeypatch) -> None:
    async def missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(
        repository_loader.asyncio,
        "create_subprocess_exec",
        missing_git,
    )

    with pytest.raises(RuntimeError, match="git executable not found"):
        await repository_loader._clone_repository_async(
            "https://github.com/example/repo.git",
            Path("/tmp/repo"),
        )


@pytest.mark.asyncio
async def test_clone_repository_async_normalizes_os_startup_errors(monkeypatch) -> None:
    async def fail_to_start(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(
        repository_loader.asyncio,
        "create_subprocess_exec",
        fail_to_start,
    )

    with pytest.raises(RuntimeError, match="failed to start git clone"):
        await repository_loader._clone_repository_async(
            "https://github.com/example/repo.git",
            Path("/tmp/repo"),
        )


@pytest.mark.asyncio
async def test_clone_repository_async_redacts_startup_error_credentials(
    monkeypatch,
) -> None:
    async def fail_to_start(*args, **kwargs):
        raise OSError(
            "failed for https://x-access-token:secret@github.com/example/repo.git"
        )

    monkeypatch.setattr(
        repository_loader.asyncio,
        "create_subprocess_exec",
        fail_to_start,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await repository_loader._clone_repository_async(
            "https://github.com/example/repo.git",
            Path("/tmp/repo"),
        )

    message = str(exc_info.value)
    assert "secret" not in message
    assert "https://***@github.com/example/repo.git" in message


@pytest.mark.asyncio
async def test_clone_repository_async_disables_interactive_git_prompts(
    monkeypatch,
) -> None:
    captured_args = ()
    captured_kwargs = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal captured_args
        captured_args = args
        captured_kwargs.update(kwargs)
        return _SuccessfulProcess()

    monkeypatch.setattr(
        repository_loader.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    await repository_loader._clone_repository_async(
        "https://github.com/example/repo.git",
        Path("/tmp/repo"),
    )

    assert captured_args[:6] == (
        "git",
        "clone",
        "--depth",
        "1",
        "--",
        "https://github.com/example/repo.git",
    )
    assert captured_kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured_kwargs["env"]["GCM_INTERACTIVE"] == "never"


def test_clone_repository_sync_normalizes_timeout(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(repository_loader.subprocess, "run", fake_run)
    monkeypatch.setattr(repository_loader, "CLONE_TIMEOUT_SECONDS", 12)

    with pytest.raises(RuntimeError, match="timed out after 12s"):
        repository_loader._clone_repository_sync(
            "https://github.com/example/repo.git",
            tmp_path / "repo",
        )


def test_clone_repository_sync_normalizes_missing_git(monkeypatch, tmp_path: Path) -> None:
    def missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(repository_loader.subprocess, "run", missing_git)

    with pytest.raises(RuntimeError, match="git executable not found"):
        repository_loader._clone_repository_sync(
            "https://github.com/example/repo.git",
            tmp_path / "repo",
        )


def test_clone_repository_sync_normalizes_os_startup_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_to_start(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(repository_loader.subprocess, "run", fail_to_start)

    with pytest.raises(RuntimeError, match="failed to start git clone"):
        repository_loader._clone_repository_sync(
            "https://github.com/example/repo.git",
            tmp_path / "repo",
        )


def test_clone_repository_sync_redacts_startup_error_credentials(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_to_start(*args, **kwargs):
        raise OSError(
            "failed for https://x-access-token:secret@github.com/example/repo.git"
        )

    monkeypatch.setattr(repository_loader.subprocess, "run", fail_to_start)

    with pytest.raises(RuntimeError) as exc_info:
        repository_loader._clone_repository_sync(
            "https://github.com/example/repo.git",
            tmp_path / "repo",
        )

    message = str(exc_info.value)
    assert "secret" not in message
    assert "https://***@github.com/example/repo.git" in message


def test_clone_repository_sync_disables_interactive_git_prompts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured_command = []
    captured_kwargs = {}

    def fake_run(*args, **kwargs):
        nonlocal captured_command
        captured_command = args[0]
        captured_kwargs.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(repository_loader.subprocess, "run", fake_run)

    repository_loader._clone_repository_sync(
        "https://github.com/example/repo.git",
        tmp_path / "repo",
    )

    assert captured_command[:6] == [
        "git",
        "clone",
        "--depth",
        "1",
        "--",
        "https://github.com/example/repo.git",
    ]
    assert captured_kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured_kwargs["env"]["GCM_INTERACTIVE"] == "never"


def test_clone_repository_sync_uses_utf8_replacement_decoding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured_kwargs = {}

    def fake_run(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return subprocess.CompletedProcess(args[0], 128, "", "fatal: bad byte \ufffd")

    monkeypatch.setattr(repository_loader.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="git clone failed \\(exit 128\\): fatal: bad byte"):
        repository_loader._clone_repository_sync(
            "https://github.com/example/repo.git",
            tmp_path / "repo",
        )

    assert captured_kwargs["text"] is True
    assert captured_kwargs["encoding"] == "utf-8"
    assert captured_kwargs["errors"] == "replace"


def test_clone_repository_sync_falls_back_to_stdout_when_stderr_is_empty(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 128, "fatal: repository not found", "")

    monkeypatch.setattr(repository_loader.subprocess, "run", fake_run)

    with pytest.raises(
        RuntimeError,
        match="git clone failed \\(exit 128\\): fatal: repository not found",
    ):
        repository_loader._clone_repository_sync(
            "https://github.com/example/repo.git",
            tmp_path / "repo",
        )


def test_clone_error_text_redacts_authenticated_clone_url() -> None:
    error = repository_loader._clone_error_text(
        "fatal: could not read from https://x-access-token:secret@github.com/owner/repo.git",
        "",
    )

    assert error == (
        "fatal: could not read from https://***@github.com/owner/repo.git"
    )
    assert "secret" not in error


def test_clone_error_text_redacts_username_only_clone_url() -> None:
    error = repository_loader._clone_error_text(
        "fatal: could not read from https://ghp_secret@github.com/owner/repo.git",
        "",
    )

    assert error == (
        "fatal: could not read from https://***@github.com/owner/repo.git"
    )
    assert "ghp_secret" not in error


def test_clone_error_text_redacts_non_https_clone_url_credentials() -> None:
    error = repository_loader._clone_error_text(
        "fatal: could not read from http://user:secret@example.com/owner/repo.git",
        "",
    )

    assert error == (
        "fatal: could not read from http://***@example.com/owner/repo.git"
    )
    assert "secret" not in error


def test_clone_error_text_redacts_uppercase_scheme_credentials() -> None:
    error = repository_loader._clone_error_text(
        "fatal: could not read from SSH://user:secret@example.com/owner/repo.git",
        "",
    )

    assert error == (
        "fatal: could not read from SSH://***@example.com/owner/repo.git"
    )
    assert "secret" not in error


def test_clone_error_text_redacts_query_tokens_bearer_tokens_and_emails() -> None:
    error = repository_loader._clone_error_text(
        "fatal: could not read from https://github.com/owner/repo.git"
        "?access_token=query-secret authorization=Bearer bearer-secret "
        "and https://github.com/owner/repo.git#access_token=fragment-value "
        "for operator@example.test",
        "",
    )

    assert "query-secret" not in error
    assert "fragment-value" not in error
    assert "bearer-secret" not in error
    assert "operator@example.test" not in error
    assert "access_token=***" in error
    assert "authorization=Bearer ***" in error
    assert "[redacted-email]" in error


def test_cloned_repository_list_files_uses_repo_relative_skip_dirs(tmp_path: Path) -> None:
    repo_path = tmp_path / "node_modules" / "repo"
    repo_path.mkdir(parents=True)
    (repo_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    skipped = repo_path / "node_modules"
    skipped.mkdir()
    (skipped / "generated.js").write_text("console.log('skip')\n", encoding="utf-8")
    repo = repository_loader.ClonedRepository(
        working_dir=tmp_path,
        repo_path=repo_path,
        graph=object(),  # type: ignore[arg-type]
    )

    assert repo.list_files() == ["app.py"]


def test_cloned_repository_list_files_prunes_skipped_dirs_without_rglob(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (repo_path / "venv" / "pkg").mkdir(parents=True)
    (repo_path / "venv" / "pkg" / "ignored.py").write_text(
        "print('skip')\n",
        encoding="utf-8",
    )

    def fail_rglob(_self, _pattern):
        raise AssertionError("list_files should prune with os.walk")

    monkeypatch.setattr(Path, "rglob", fail_rglob)

    repo = repository_loader.ClonedRepository(
        working_dir=tmp_path,
        repo_path=repo_path,
        graph=object(),  # type: ignore[arg-type]
    )

    assert repo.list_files() == ["app.py"]


def test_cloned_repository_list_files_skips_symlinks_outside_repo(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret\n", encoding="utf-8")
    (repo_path / "leak.txt").symlink_to(outside_file)
    repo = repository_loader.ClonedRepository(
        working_dir=tmp_path,
        repo_path=repo_path,
        graph=object(),  # type: ignore[arg-type]
    )

    assert repo.list_files() == ["app.py"]


def test_cloned_repository_read_text_normalizes_max_chars(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "app.py").write_text("abcdef", encoding="utf-8")
    repo = repository_loader.ClonedRepository(
        working_dir=tmp_path,
        repo_path=repo_path,
        graph=object(),  # type: ignore[arg-type]
    )

    assert repo.read_text("app.py", max_chars=-3) == ""
    assert repo.read_text("app.py", max_chars="bad") == "abcdef"
    assert repo.read_text("app.py", max_chars=float("inf")) == "abcdef"
    assert repo.read_text("app.py", max_chars=3) == "abc"


def test_cloned_repository_read_text_uses_bounded_stream_read(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "app.py").write_text("abcdef", encoding="utf-8")
    repo = repository_loader.ClonedRepository(
        working_dir=tmp_path,
        repo_path=repo_path,
        graph=object(),  # type: ignore[arg-type]
    )

    def fail_read_text(*args, **kwargs):
        raise AssertionError("read_text should stream only the requested prefix")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    assert repo.read_text("app.py", max_chars=3) == "abc"


@pytest.mark.parametrize("relative_path", ["", "   ", None, {"path": "app.py"}])
def test_cloned_repository_read_text_rejects_malformed_paths(
    tmp_path: Path,
    relative_path,
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = repository_loader.ClonedRepository(
        working_dir=tmp_path,
        repo_path=repo_path,
        graph=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="relative_path must be a non-empty string"):
        repo.read_text(relative_path)  # type: ignore[arg-type]


@pytest.mark.parametrize("control_char", ["\x00", "\n", "\t", "\x7f"])
def test_cloned_repository_read_text_rejects_control_path_segments(
    tmp_path: Path,
    control_char: str,
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = repository_loader.ClonedRepository(
        working_dir=tmp_path,
        repo_path=repo_path,
        graph=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="invalid path segment"):
        repo.read_text(f"bad{control_char}name.py")


@pytest.mark.parametrize("relative_path", ["missing.py", "pkg"])
def test_cloned_repository_read_text_rejects_missing_or_non_file_paths(
    tmp_path: Path,
    relative_path: str,
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "pkg").mkdir()
    repo = repository_loader.ClonedRepository(
        working_dir=tmp_path,
        repo_path=repo_path,
        graph=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="not a file"):
        repo.read_text(relative_path)


def test_build_authenticated_clone_url_injects_token_for_exact_github_host() -> None:
    clone_url = "https://github.com/example/repo.git"

    auth_url = repository_loader._build_authenticated_clone_url(clone_url, "tok en")

    assert auth_url == "https://x-access-token:tok%20en@github.com/example/repo.git"


def test_build_authenticated_clone_url_trims_clone_url() -> None:
    auth_url = repository_loader._build_authenticated_clone_url(
        "  https://github.com/example/repo.git  ",
        "secret",
    )

    assert auth_url == "https://x-access-token:secret@github.com/example/repo.git"


def test_build_authenticated_clone_url_rejects_nul_clone_url() -> None:
    with pytest.raises(ValueError, match="invalid character"):
        repository_loader._build_authenticated_clone_url(
            "https://github.com/example/repo.git\x00",
            "secret",
        )


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://github.com/example/repo .git",
        "git@github.com:example/repo .git",
    ],
)
def test_build_authenticated_clone_url_rejects_whitespace_clone_url(
    clone_url: str,
) -> None:
    with pytest.raises(ValueError, match="invalid character"):
        repository_loader._build_authenticated_clone_url(clone_url, "secret")


@pytest.mark.parametrize("control_char", ["\r", "\n", "\t", "\x7f"])
def test_build_authenticated_clone_url_rejects_control_clone_url(
    control_char: str,
) -> None:
    with pytest.raises(ValueError, match="invalid character"):
        repository_loader._build_authenticated_clone_url(
            f"https://github.com/example/{control_char}repo.git",
            "secret",
        )


def test_build_authenticated_clone_url_rejects_lookalike_hosts() -> None:
    clone_url = "https://github.com.evil.example/repo.git"

    auth_url = repository_loader._build_authenticated_clone_url(clone_url, "secret")

    assert auth_url == clone_url


def test_build_authenticated_clone_url_ignores_blank_token() -> None:
    clone_url = "https://github.com/example/repo.git"

    auth_url = repository_loader._build_authenticated_clone_url(clone_url, "   ")

    assert auth_url == clone_url


@pytest.mark.parametrize("control_char", ["\r", "\n", "\t", "\x7f"])
def test_build_authenticated_clone_url_ignores_control_character_token(
    control_char: str,
) -> None:
    clone_url = "https://github.com/example/repo.git"

    auth_url = repository_loader._build_authenticated_clone_url(
        clone_url,
        f"secret{control_char}extra",
    )

    assert auth_url == clone_url


def test_build_authenticated_clone_url_ignores_malformed_token_type() -> None:
    clone_url = "https://github.com/example/repo.git"

    auth_url = repository_loader._build_authenticated_clone_url(  # type: ignore[arg-type]
        clone_url,
        {"token": "secret"},
    )

    assert auth_url == clone_url


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://github.com:not-a-port/example/repo.git",
        "https://github.com:0/example/repo.git",
    ],
)
def test_build_authenticated_clone_url_rejects_invalid_github_port(
    clone_url: str,
) -> None:
    with pytest.raises(ValueError, match="invalid port"):
        repository_loader._build_authenticated_clone_url(
            clone_url,
            "secret",
        )


@pytest.mark.parametrize(
    ("clone_url", "match"),
    [
        ("https://github.com:not-a-port/example/repo.git", "invalid port"),
        ("https://github.com:0/example/repo.git", "invalid port"),
        ("https:///example/repo.git", "must include a host"),
        (
            "https://user:secret@github.com/example/repo.git",
            "must not include credentials",
        ),
        (
            "ssh://git:secret@github.com/example/repo.git",
            "must not include credentials",
        ),
        (
            "ssh://root@github.com/example/repo.git",
            "must not include credentials",
        ),
        (
            "git+ssh://root@github.com/example/repo.git",
            "must not include credentials",
        ),
        (
            "git://git@github.com/example/repo.git",
            "must not include credentials",
        ),
        (
            "https://github.com/example/repo.git?access_token=secret",
            "must not include query or fragment",
        ),
        (
            "https://github.com/example/repo.git#readme",
            "must not include query or fragment",
        ),
        ("ftp://github.com/example/repo.git", "scheme is not allowed"),
        ("javascript://github.com/example/repo.git", "scheme is not allowed"),
        ("example/repo", "supported remote URL"),
        ("/tmp/repo.git", "supported remote URL"),
        ("github.com:example/repo.git", "supported remote URL"),
        ("git@gitlab.com:example/repo.git", "supported remote URL"),
    ],
)
def test_normalize_clone_url_rejects_malformed_url_style_clone_url(
    clone_url: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        repository_loader._normalize_clone_url(clone_url)


@pytest.mark.parametrize(
    "clone_url",
    [
        "git@github.com:example/repo.git",
        "git@www.github.com:example/repo.git",
        "ssh://git@github.com/example/repo.git",
        "git+ssh://git@github.com/example/repo.git",
    ],
)
def test_normalize_clone_url_accepts_safe_ssh_git_user_urls(
    clone_url: str,
) -> None:
    assert repository_loader._normalize_clone_url(clone_url) == clone_url


def test_normalize_clone_url_rejects_scp_style_query_fragments() -> None:
    with pytest.raises(ValueError, match="must not include query or fragment"):
        repository_loader._normalize_clone_url(
            "git@github.com:example/repo.git?access_token=secret",
        )


@pytest.mark.parametrize(
    "clone_url",
    ["", "   ", None, {"url": "https://github.com/example/repo.git"}],
)
def test_build_authenticated_clone_url_rejects_malformed_clone_url(clone_url) -> None:
    with pytest.raises(ValueError, match="clone_url must be a non-empty string"):
        repository_loader._build_authenticated_clone_url(  # type: ignore[arg-type]
            clone_url,
            "secret",
        )


def test_build_graph_from_clone_url_sync_rejects_bad_clone_url_before_tempdir(
    monkeypatch,
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("temporary clone directory should not be created")

    monkeypatch.setattr(repository_loader.tempfile, "mkdtemp", fail_if_called)

    with pytest.raises(ValueError, match="clone_url must be a non-empty string"):
        repository_loader.build_graph_from_clone_url_sync(  # type: ignore[arg-type]
            {"url": "https://github.com/example/repo.git"},
        )


def test_build_graph_from_clone_url_sync_requires_graph_before_tempdir(
    monkeypatch,
) -> None:
    def fail_graph():
        raise RuntimeError("networkx missing")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("temporary clone directory should not be created")

    monkeypatch.setattr(repository_loader, "CodebaseGraph", fail_graph)
    monkeypatch.setattr(repository_loader.tempfile, "mkdtemp", fail_if_called)

    with pytest.raises(RuntimeError, match="networkx missing"):
        repository_loader.build_graph_from_clone_url_sync(
            "https://github.com/example/repo.git",
        )


def test_build_graph_from_clone_url_sync_rejects_bad_auth_url_before_tempdir(
    monkeypatch,
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("graph/tempdir should not be created")

    monkeypatch.setattr(repository_loader, "CodebaseGraph", fail_if_called)
    monkeypatch.setattr(repository_loader.tempfile, "mkdtemp", fail_if_called)

    with pytest.raises(ValueError, match="invalid port"):
        repository_loader.build_graph_from_clone_url_sync(
            "https://github.com:not-a-port/example/repo.git",
            token="secret",
        )


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://github.com:not-a-port/example/repo.git",
        "https:///example/repo.git",
    ],
)
def test_build_graph_from_clone_url_sync_rejects_bad_url_before_tempdir(
    monkeypatch,
    clone_url: str,
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("graph/tempdir should not be created")

    monkeypatch.setattr(repository_loader, "CodebaseGraph", fail_if_called)
    monkeypatch.setattr(repository_loader.tempfile, "mkdtemp", fail_if_called)

    with pytest.raises(ValueError):
        repository_loader.build_graph_from_clone_url_sync(clone_url)


@pytest.mark.asyncio
async def test_build_graph_from_clone_url_rejects_bad_auth_url_before_tempdir(
    monkeypatch,
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("graph/tempdir should not be created")

    monkeypatch.setattr(repository_loader, "CodebaseGraph", fail_if_called)
    monkeypatch.setattr(repository_loader.tempfile, "mkdtemp", fail_if_called)

    with pytest.raises(ValueError, match="invalid port"):
        await repository_loader.build_graph_from_clone_url(
            "https://github.com:not-a-port/example/repo.git",
            token="secret",
        )


@pytest.mark.asyncio
async def test_clone_repository_async_replaces_non_utf8_stderr(monkeypatch) -> None:
    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FailedProcess()

    monkeypatch.setattr(
        repository_loader.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(RuntimeError, match="git clone failed \\(exit 128\\): fatal: bad byte"):
        await repository_loader._clone_repository_async(
            "https://github.com/example/repo.git",
            Path("/tmp/repo"),
        )


@pytest.mark.asyncio
async def test_clone_repository_async_falls_back_to_stdout_when_stderr_is_empty(
    monkeypatch,
) -> None:
    async def fake_create_subprocess_exec(*args, **kwargs):
        return _StdoutOnlyFailedProcess()

    monkeypatch.setattr(
        repository_loader.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(
        RuntimeError,
        match="git clone failed \\(exit 128\\): fatal: repository not found",
    ):
        await repository_loader._clone_repository_async(
            "https://github.com/example/repo.git",
            Path("/tmp/repo"),
        )
