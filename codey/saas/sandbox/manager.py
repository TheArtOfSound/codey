from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60  # seconds
MAX_TIMEOUT = 300
SANDBOX_ROOT = Path(tempfile.gettempdir()) / "codey_sandboxes"


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

    async def create(
        self, sandbox_id: str, user_id: str, session_id: str, timeout: int
    ) -> Sandbox:
        e2b_sandbox = self._E2BSandbox(
            api_key=os.environ["E2B_API_KEY"],
            timeout=timeout,
        )
        self._e2b_sandboxes[sandbox_id] = e2b_sandbox

        # Create a local mirror path for metadata
        root = SANDBOX_ROOT / sandbox_id
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        sandbox = Sandbox(
            id=sandbox_id,
            user_id=user_id,
            session_id=session_id,
            root=root,
            timeout=min(timeout, MAX_TIMEOUT),
        )
        logger.info(
            "E2B sandbox %s created for user=%s session=%s",
            sandbox_id,
            user_id,
            session_id,
        )
        return sandbox

    async def destroy(self, sandbox: Sandbox) -> None:
        e2b = self._e2b_sandboxes.pop(sandbox.id, None)
        if e2b is not None:
            try:
                e2b.close()  # type: ignore[union-attr]
            except Exception:
                logger.exception("Failed to close E2B sandbox %s", sandbox.id)
        try:
            shutil.rmtree(sandbox.root)
        except Exception:
            logger.exception("Failed to remove local mirror for sandbox %s", sandbox.id)

    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout: int,
        env: dict[str, str] | None,
    ) -> CommandResult:
        e2b = self._e2b_sandboxes.get(sandbox.id)
        if e2b is None:
            raise ValueError(f"E2B sandbox not found: {sandbox.id}")

        try:
            result = e2b.process.start_and_wait(  # type: ignore[union-attr]
                command,
                timeout=timeout,
                env_vars=env or {},
            )
            return CommandResult(
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
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
            logger.exception("E2B execution failed in sandbox %s", sandbox.id)
            return CommandResult(exit_code=-1, stdout="", stderr=str(exc))


# ---------------------------------------------------------------------------
# Local Backend (fallback)
# ---------------------------------------------------------------------------


class LocalSandboxBackend:
    """Runs code in local temp directories with subprocess isolation."""

    async def create(
        self, sandbox_id: str, user_id: str, session_id: str, timeout: int
    ) -> Sandbox:
        root = SANDBOX_ROOT / sandbox_id
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        # Create common subdirectories
        (workspace / "src").mkdir(exist_ok=True)
        (workspace / "tests").mkdir(exist_ok=True)

        # Write a minimal .gitignore
        (workspace / ".gitignore").write_text(
            "__pycache__/\n*.pyc\nnode_modules/\n.env\nvenv/\n"
        )

        sandbox = Sandbox(
            id=sandbox_id,
            user_id=user_id,
            session_id=session_id,
            root=root,
            timeout=min(timeout, MAX_TIMEOUT),
        )
        logger.info(
            "Local sandbox %s created for user=%s session=%s at %s",
            sandbox_id,
            user_id,
            session_id,
            root,
        )
        return sandbox

    async def destroy(self, sandbox: Sandbox) -> None:
        try:
            shutil.rmtree(sandbox.root)
        except Exception:
            logger.exception("Failed to remove sandbox %s", sandbox.id)

    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout: int,
        env: dict[str, str] | None,
    ) -> CommandResult:
        run_env = {
            "HOME": str(sandbox.root),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color",
        }
        if env:
            run_env.update(env)

        # Disallow escaping the sandbox root via env manipulation
        run_env.pop("LD_PRELOAD", None)
        run_env.pop("LD_LIBRARY_PATH", None)

        timed_out = False
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(sandbox.workspace),
                env=run_env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()  # type: ignore[union-attr]
            stdout_bytes, stderr_bytes = b"", b"Command timed out"
            exit_code = -1
        except Exception as exc:
            logger.exception("Command execution failed in sandbox %s", sandbox.id)
            return CommandResult(exit_code=-1, stdout="", stderr=str(exc))

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


def _select_backend() -> E2BSandboxBackend | LocalSandboxBackend:
    """Pick E2B when the API key is present, otherwise fall back to local."""
    if os.environ.get("E2B_API_KEY"):
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
        sandbox = await self._backend.create(
            sandbox_id, user_id, session_id, min(timeout, MAX_TIMEOUT)
        )
        self._sandboxes[sandbox_id] = sandbox
        return sandbox

    async def destroy(self, sandbox_id: str) -> None:
        """Remove a sandbox and all its contents."""
        sandbox = self._get(sandbox_id)
        await self._backend.destroy(sandbox)
        self._sandboxes.pop(sandbox_id, None)
        logger.info("Sandbox %s destroyed", sandbox_id)

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
        effective_timeout = min(timeout or sandbox.timeout, MAX_TIMEOUT)
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
        if recursive:
            for p in base.rglob("*"):
                if p.is_file():
                    files.append(str(p.relative_to(sandbox.workspace)))
        else:
            for p in base.iterdir():
                rel = str(p.relative_to(sandbox.workspace))
                files.append(rel + "/" if p.is_dir() else rel)

        files.sort()
        return files

    async def download_zip(self, sandbox_id: str) -> bytes:
        """Create a ZIP archive of the entire sandbox workspace."""
        sandbox = self._get(sandbox_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for filepath in sandbox.workspace.rglob("*"):
                if filepath.is_file():
                    arcname = str(filepath.relative_to(sandbox.workspace))
                    # Skip large binary files
                    if filepath.stat().st_size > 50_000_000:  # 50 MB
                        logger.warning(
                            "Skipping large file %s (%d bytes)",
                            arcname,
                            filepath.stat().st_size,
                        )
                        continue
                    zf.write(filepath, arcname)
        return buf.getvalue()

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _get(self, sandbox_id: str) -> Sandbox:
        """Retrieve a sandbox by ID or raise."""
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise ValueError(f"Sandbox not found: {sandbox_id}")
        return sandbox

    def _resolve_path(self, sandbox: Sandbox, path: str) -> Path:
        """Resolve *path* relative to the workspace, preventing traversal."""
        resolved = (sandbox.workspace / path).resolve()
        if not str(resolved).startswith(str(sandbox.workspace.resolve())):
            raise PermissionError(
                f"Path traversal blocked: '{path}' resolves outside sandbox"
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
        for sid in expired:
            await self.destroy(sid)
        return len(expired)
