from __future__ import annotations

import asyncio
import inspect
import io
import logging
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from codey.saas.archive_utils import dedupe_archive_path, safe_archive_path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60  # seconds
MAX_TIMEOUT = 300
SANDBOX_ROOT = Path(tempfile.gettempdir()) / "codey_sandboxes"
_SANDBOX_DRAIN_TIMEOUT_SECONDS = 5.0
_PROTECTED_LOCAL_ENV_KEYS = frozenset({
    "HOME",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "OLDPWD",
    "PWD",
})
_SANDBOX_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_URL_CREDENTIAL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|token|secret)=)[^&\s]+"
)
_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|token|secret|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_sandbox_timeout(
    value: object,
    default: object = DEFAULT_TIMEOUT,
) -> int:
    def parse(raw: object) -> int | None:
        if isinstance(raw, bool):
            return None
        try:
            normalized = int(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        return normalized if normalized > 0 else None

    timeout = parse(value) or parse(default) or DEFAULT_TIMEOUT
    return min(timeout, MAX_TIMEOUT)


def _coerce_sandbox_exit_code(value: object, fallback: int = -1) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback


def _coerce_sandbox_output(value: object, max_chars: int = 1_000_000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    return text[:max_chars]


def _redact_sandbox_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _NAMED_SECRET_RE.sub(r"\1***", text)
    return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)


def _sandbox_root_for_id(sandbox_id: str) -> Path:
    if (
        not isinstance(sandbox_id, str)
        or not sandbox_id
        or _has_ascii_control(sandbox_id)
        or not _SANDBOX_ID_RE.fullmatch(sandbox_id)
    ):
        raise ValueError("Invalid sandbox id")

    sandbox_root = SANDBOX_ROOT.resolve()
    root = (SANDBOX_ROOT / sandbox_id).resolve()
    if sandbox_root not in {root, *root.parents}:
        raise ValueError("Invalid sandbox id")
    return root


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass
class Sandbox:
    id: str
    user_id: str
    session_id: str
    root: Path
    created_at: float = field(default_factory=lambda: __import__("time").time())
    timeout: int = DEFAULT_TIMEOUT

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"


class SandboxBackend(Protocol):
    """Protocol for pluggable sandbox backends."""

    async def create(
        self, sandbox_id: str, user_id: str, session_id: str, timeout: int
    ) -> Sandbox: ...

    async def destroy(self, sandbox: Sandbox) -> None: ...

    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout: int,
        env: dict[str, str] | None,
    ) -> CommandResult: ...


# ---------------------------------------------------------------------------
# E2B Cloud Backend
# ---------------------------------------------------------------------------


class E2BSandboxBackend:
    """Runs code inside E2B cloud sandboxes for full isolation."""

    def __init__(self) -> None:
        try:
            from e2b_code_interpreter import Sandbox as E2BSandbox  # noqa: F401

            self._E2BSandbox = E2BSandbox
        except ImportError as exc:
            raise RuntimeError(
                "e2b-code-interpreter is required for E2B backend. "
                "Install with: pip install e2b-code-interpreter"
            ) from exc

        self._e2b_sandboxes: dict[str, object] = {}

    async def _close_sandbox(self, e2b: object) -> None:
        close = getattr(e2b, "close", None)
        if not callable(close):
            return

        result = close()
        if inspect.isawaitable(result):
            await result

    async def create(
        self, sandbox_id: str, user_id: str, session_id: str, timeout: int
    ) -> Sandbox:
        effective_timeout = _coerce_sandbox_timeout(timeout)
        root = _sandbox_root_for_id(sandbox_id)
        api_key = _coerce_non_empty_sandbox_secret("E2B_API_KEY")
        if api_key is None:
            raise RuntimeError("E2B_API_KEY must be set to create an E2B sandbox")

        e2b_sandbox = self._E2BSandbox(
            api_key=api_key,
            timeout=effective_timeout,
        )
        self._e2b_sandboxes[sandbox_id] = e2b_sandbox

        # Create a local mirror path for metadata
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        sandbox = Sandbox(
            id=sandbox_id,
            user_id=user_id,
            session_id=session_id,
            root=root,
            timeout=effective_timeout,
        )
        logger.info(
            "E2B sandbox %s created for user=%s session=%s",
            _redact_sandbox_error(sandbox_id),
            _redact_sandbox_error(user_id),
            _redact_sandbox_error(session_id),
        )
        return sandbox

    async def destroy(self, sandbox: Sandbox) -> None:
        e2b = self._e2b_sandboxes.pop(sandbox.id, None)
        if e2b is not None:
            try:
                await self._close_sandbox(e2b)
            except Exception as exc:
                logger.warning(
                    "Failed to close E2B sandbox %s: %s",
                    _redact_sandbox_error(sandbox.id),
                    _redact_sandbox_error(exc),
                )
        try:
            shutil.rmtree(sandbox.root)
        except Exception as exc:
            logger.warning(
                "Failed to remove local mirror for sandbox %s: %s",
                _redact_sandbox_error(sandbox.id),
                _redact_sandbox_error(exc),
            )

    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout: int,
        env: dict[str, str] | None,
    ) -> CommandResult:
        effective_timeout = _coerce_sandbox_timeout(timeout, sandbox.timeout)
        e2b = self._e2b_sandboxes.get(sandbox.id)
        if e2b is None:
            raise ValueError(f"E2B sandbox not found: {sandbox.id}")

        try:
            result = e2b.process.start_and_wait(  # type: ignore[union-attr]
                command,
                timeout=effective_timeout,
                env_vars=env or {},
            )
            return CommandResult(
                exit_code=_coerce_sandbox_exit_code(
                    getattr(result, "exit_code", None)
                ),
                stdout=_coerce_sandbox_output(getattr(result, "stdout", "")),
                stderr=_coerce_sandbox_output(getattr(result, "stderr", "")),
                timed_out=False,
            )
        except TimeoutError:
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr="Command timed out in E2B sandbox",
                timed_out=True,
            )
        except Exception as exc:
            safe_error = _redact_sandbox_error(exc)
            logger.warning(
                "E2B execution failed in sandbox %s: %s",
                _redact_sandbox_error(sandbox.id),
                safe_error,
            )
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=safe_error,
            )


# ---------------------------------------------------------------------------
# Local Backend (fallback)
# ---------------------------------------------------------------------------


class LocalSandboxBackend:
    """Runs code in local temp directories with subprocess isolation."""

    def _build_env(self, sandbox: Sandbox, env: dict[str, str] | None) -> dict[str, str]:
        run_env = {
            "HOME": str(sandbox.root),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color",
        }
        if not env:
            return run_env

        for key, value in env.items():
            if (
                not isinstance(key, str)
                or not key
                or "=" in key
                or _has_ascii_control(key)
                or key in _PROTECTED_LOCAL_ENV_KEYS
                or not isinstance(value, str)
                or _has_ascii_control(value)
            ):
                continue
            run_env[key] = value

        return run_env

    async def create(
        self, sandbox_id: str, user_id: str, session_id: str, timeout: int
    ) -> Sandbox:
        effective_timeout = _coerce_sandbox_timeout(timeout)
        root = _sandbox_root_for_id(sandbox_id)
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        # Create common subdirectories
        (workspace / "src").mkdir(exist_ok=True)
        (workspace / "tests").mkdir(exist_ok=True)

        # Write a minimal .gitignore
        (workspace / ".gitignore").write_text(
            "__pycache__/\n*.pyc\nnode_modules/\n.env\nvenv/\n",
            encoding="utf-8",
        )

        sandbox = Sandbox(
            id=sandbox_id,
            user_id=user_id,
            session_id=session_id,
            root=root,
            timeout=effective_timeout,
        )
        logger.info(
            "Local sandbox %s created for user=%s session=%s at %s",
            _redact_sandbox_error(sandbox_id),
            _redact_sandbox_error(user_id),
            _redact_sandbox_error(session_id),
            _redact_sandbox_error(root),
        )
        return sandbox

    async def destroy(self, sandbox: Sandbox) -> None:
        try:
            shutil.rmtree(sandbox.root)
        except Exception as exc:
            logger.warning(
                "Failed to remove sandbox %s: %s",
                _redact_sandbox_error(sandbox.id),
                _redact_sandbox_error(exc),
            )

    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout: int,
        env: dict[str, str] | None,
    ) -> CommandResult:
        effective_timeout = _coerce_sandbox_timeout(timeout, sandbox.timeout)
        run_env = self._build_env(sandbox, env)

        timed_out = False
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(sandbox.workspace),
                env=run_env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=effective_timeout
            )
            exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            timed_out = True
            if proc is not None and proc.returncode is None:
                should_drain = True
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                except Exception as exc:
                    should_drain = False
                    logger.warning(
                        "Failed to kill timed-out sandbox process %s: %s",
                        _redact_sandbox_error(sandbox.id),
                        _redact_sandbox_error(exc),
                    )
                if should_drain:
                    try:
                        await asyncio.wait_for(
                            proc.communicate(),
                            timeout=_SANDBOX_DRAIN_TIMEOUT_SECONDS,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to drain timed-out sandbox process %s: %s",
                            _redact_sandbox_error(sandbox.id),
                            _redact_sandbox_error(exc),
                        )
            stdout_bytes, stderr_bytes = b"", b"Command timed out"
            exit_code = -1
        except Exception as exc:
            safe_error = _redact_sandbox_error(exc)
            logger.warning(
                "Command execution failed in sandbox %s: %s",
                _redact_sandbox_error(sandbox.id),
                safe_error,
            )
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=safe_error,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Cap output size to avoid memory issues
        max_output = 1_000_000  # 1 MB
        stdout = stdout[:max_output]
        stderr = stderr[:max_output]

        return CommandResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def _coerce_non_empty_sandbox_secret(name: str) -> str | None:
    value = os.environ.get(name)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None
    return normalized or None


def _select_backend() -> E2BSandboxBackend | LocalSandboxBackend:
    """Pick E2B when the API key is present, otherwise fall back to local."""
    if _coerce_non_empty_sandbox_secret("E2B_API_KEY"):
        try:
            return E2BSandboxBackend()
        except RuntimeError:
            logger.warning(
                "E2B_API_KEY set but e2b-code-interpreter not installed; "
                "falling back to local sandbox backend."
            )
    return LocalSandboxBackend()


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class SandboxManager:
    """Manages isolated workspace directories for code execution.

    Uses E2B cloud sandboxes when ``E2B_API_KEY`` is set, otherwise falls
    back to local temp directories with subprocess isolation.
    """

    def __init__(self) -> None:
        self._sandboxes: dict[str, Sandbox] = {}
        self._backend = _select_backend()
        SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        logger.info("SandboxManager using backend: %s", type(self._backend).__name__)

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def create(
        self,
        user_id: str,
        session_id: str,
        *,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Sandbox:
        """Create an isolated workspace and return a :class:`Sandbox` handle."""
        sandbox_id = uuid.uuid4().hex[:16]
        effective_timeout = _coerce_sandbox_timeout(timeout)
        sandbox = await self._backend.create(
            sandbox_id, user_id, session_id, effective_timeout
        )
        self._sandboxes[sandbox_id] = sandbox
        return sandbox

    async def destroy(self, sandbox_id: str) -> None:
        """Remove a sandbox and all its contents."""
        sandbox = self._get(sandbox_id)
        try:
            await self._backend.destroy(sandbox)
        finally:
            self._sandboxes.pop(sandbox_id, None)
        logger.info("Sandbox %s destroyed", _redact_sandbox_error(sandbox_id))

    # -----------------------------------------------------------------------
    # Command execution
    # -----------------------------------------------------------------------

    async def execute(
        self,
        sandbox_id: str,
        command: str,
        *,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run *command* inside the sandbox workspace.

        Delegates to the active backend (E2B or local subprocess).
        """
        sandbox = self._get(sandbox_id)
        effective_timeout = _coerce_sandbox_timeout(timeout, sandbox.timeout)
        return await self._backend.execute(sandbox, command, effective_timeout, env)

    # -----------------------------------------------------------------------
    # File operations
    # -----------------------------------------------------------------------

    async def write_file(
        self, sandbox_id: str, path: str, content: str
    ) -> None:
        """Write *content* to a file at *path* (relative to workspace)."""
        sandbox = self._get(sandbox_id)
        target = self._resolve_path(sandbox, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def read_file(self, sandbox_id: str, path: str) -> str:
        """Read a file from the sandbox workspace."""
        sandbox = self._get(sandbox_id)
        target = self._resolve_path(sandbox, path)
        if not target.exists():
            raise FileNotFoundError(f"File not found in sandbox: {path}")
        return target.read_text(encoding="utf-8")

    async def list_files(
        self, sandbox_id: str, path: str = ".", *, recursive: bool = True
    ) -> list[str]:
        """List files in the sandbox workspace."""
        sandbox = self._get(sandbox_id)
        base = self._resolve_path(sandbox, path)
        if not base.exists():
            return []

        files: list[str] = []
        workspace_root = sandbox.workspace.resolve()
        if recursive:
            for p in base.rglob("*"):
                if p.is_symlink():
                    continue
                if p.is_file():
                    files.append(str(p.relative_to(workspace_root)))
        else:
            for p in base.iterdir():
                if p.is_symlink():
                    continue
                rel = str(p.relative_to(workspace_root))
                files.append(rel + "/" if p.is_dir() else rel)

        files.sort()
        return files

    async def download_zip(self, sandbox_id: str) -> bytes:
        """Create a ZIP archive of the entire sandbox workspace."""
        sandbox = self._get(sandbox_id)
        buf = io.BytesIO()
        seen_archive_paths: set[str] = set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for filepath in sandbox.workspace.rglob("*"):
                try:
                    is_symlink = filepath.is_symlink()
                    is_file = filepath.is_file()
                except OSError:
                    logger.warning(
                        "Skipping file that disappeared during ZIP export: %s",
                        filepath,
                    )
                    continue
                if is_symlink:
                    logger.warning(
                        "Skipping symlink during sandbox ZIP export: %s",
                        filepath,
                    )
                    continue
                if is_file:
                    relative_path = str(filepath.relative_to(sandbox.workspace))
                    arcname = dedupe_archive_path(
                        safe_archive_path(relative_path),
                        seen_archive_paths,
                    )
                    try:
                        file_size = filepath.stat().st_size
                    except OSError:
                        logger.warning(
                            "Skipping file that disappeared during ZIP export: %s",
                            arcname,
                        )
                        continue
                    # Skip large binary files
                    if file_size > 50_000_000:  # 50 MB
                        logger.warning(
                            "Skipping large file %s (%d bytes)",
                            arcname,
                            file_size,
                        )
                        continue
                    try:
                        became_symlink = filepath.is_symlink()
                    except OSError:
                        logger.warning(
                            "Skipping file that disappeared during ZIP export: %s",
                            arcname,
                        )
                        continue
                    if became_symlink:
                        logger.warning(
                            "Skipping file that became a symlink during ZIP export: %s",
                            arcname,
                        )
                        continue
                    try:
                        zf.write(filepath, arcname)
                    except OSError:
                        logger.warning(
                            "Skipping unreadable file during ZIP export: %s",
                            arcname,
                        )
        return buf.getvalue()

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _get(self, sandbox_id: str) -> Sandbox:
        """Retrieve a sandbox by ID or raise."""
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise ValueError(
                f"Sandbox not found: {_redact_sandbox_error(sandbox_id)}"
            )
        return sandbox

    def _resolve_path(self, sandbox: Sandbox, path: str) -> Path:
        """Resolve *path* relative to the workspace, preventing traversal."""
        workspace_root = sandbox.workspace.resolve()
        resolved = (workspace_root / path).resolve()
        if workspace_root not in {resolved, *resolved.parents}:
            raise PermissionError(
                "Path traversal blocked: "
                f"'{_redact_sandbox_error(path)}' resolves outside sandbox"
            )
        return resolved

    def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Return the sandbox if it exists, else ``None``."""
        return self._sandboxes.get(sandbox_id)

    async def cleanup_expired(self, max_age_seconds: int = 3600) -> int:
        """Destroy sandboxes older than *max_age_seconds*. Returns count removed."""
        import time

        now = time.time()
        expired = [
            sid
            for sid, sb in self._sandboxes.items()
            if (now - sb.created_at) > max_age_seconds
        ]
        removed = 0
        for sid in expired:
            try:
                await self.destroy(sid)
            except Exception as exc:
                logger.warning(
                    "Failed to cleanup expired sandbox %s: %s",
                    _redact_sandbox_error(sid),
                    _redact_sandbox_error(exc),
                )
            if sid not in self._sandboxes:
                removed += 1
        return removed
