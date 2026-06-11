"""Helpers for cloning and parsing connected repositories."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from codey.graph.engine import CodebaseGraph
from codey.parser.extractor import SKIP_DIRS, parse_directory

logger = logging.getLogger(__name__)

CLONE_TIMEOUT_SECONDS = 180
CLONE_DRAIN_TIMEOUT_SECONDS = 5.0
ALLOWED_CLONE_SCHEMES = {"git", "git+ssh", "http", "https", "ssh"}
ALLOWED_SCP_CLONE_HOSTS = {"github.com", "www.github.com"}
URL_USERINFO_RE = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]+)?@",
    re.IGNORECASE,
)
QUERY_SECRET_RE = re.compile(
    r"([?&#](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token)=)[^&#\s]+",
    re.IGNORECASE,
)
NAMED_SECRET_RE = re.compile(
    r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token|authorization)\b(\s*[:=]\s*)"
    r"(?:Bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def _git_clone_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    return env


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _normalize_clone_url(clone_url: str) -> str:
    if not isinstance(clone_url, str):
        raise ValueError("clone_url must be a non-empty string")

    clone_url = clone_url.strip()
    if not clone_url:
        raise ValueError("clone_url must be a non-empty string")
    if _has_ascii_control(clone_url) or _has_whitespace(clone_url):
        raise ValueError("clone_url contains an invalid character")
    if "?" in clone_url or "#" in clone_url:
        raise ValueError("clone_url must not include query or fragment")
    if "://" not in clone_url:
        user_host, separator, path = clone_url.partition(":")
        user, _, host = user_host.partition("@")
        if (
            separator != ":"
            or not path
            or user.lower() != "git"
            or host.lower() not in ALLOWED_SCP_CLONE_HOSTS
        ):
            raise ValueError("clone_url must be a supported remote URL")
    else:
        try:
            split = urlsplit(clone_url)
            port = split.port
        except ValueError as exc:
            raise ValueError("clone_url has an invalid port") from exc
        scheme = split.scheme.lower()
        if scheme not in ALLOWED_CLONE_SCHEMES:
            raise ValueError("clone_url scheme is not allowed")
        if port is not None and port <= 0:
            raise ValueError("clone_url has an invalid port")
        if split.hostname is None:
            raise ValueError("clone_url must include a host")
        if scheme in {"http", "https"} and (
            split.username is not None or split.password is not None
        ):
            raise ValueError("clone_url must not include credentials")
        if split.password is not None:
            raise ValueError("clone_url must not include credentials")
        if split.username is not None and (
            scheme not in {"git+ssh", "ssh"} or split.username.lower() != "git"
        ):
            raise ValueError("clone_url must not include credentials")

    return clone_url


def _build_authenticated_clone_url(clone_url: str, token: str | None) -> str:
    clone_url = _normalize_clone_url(clone_url)

    if not isinstance(token, str):
        return clone_url

    token = token.strip()
    if not token:
        return clone_url
    if _has_ascii_control(token):
        return clone_url

    try:
        split = urlsplit(clone_url)
        hostname = (split.hostname or "").lower()
    except ValueError as exc:
        raise ValueError("clone_url must be a valid URL for authentication") from exc

    if split.scheme != "https" or hostname not in {"github.com", "www.github.com"}:
        return clone_url

    try:
        port = split.port
    except ValueError as exc:
        raise ValueError("clone_url has an invalid port") from exc
    if port is not None and port <= 0:
        raise ValueError("clone_url has an invalid port")

    quoted_token = quote(token, safe="")
    netloc = f"x-access-token:{quoted_token}@{hostname}"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((split.scheme, netloc, split.path, split.query, split.fragment))


def _clone_error_text(stderr: str, stdout: str) -> str:
    text = stderr.strip() or stdout.strip()
    text = URL_USERINFO_RE.sub(r"\g<scheme>***@", text)
    text = QUERY_SECRET_RE.sub(r"\1***", text)

    def _replace_named_secret(match: re.Match[str]) -> str:
        prefix = f"{match.group(1)}{match.group(2)}"
        if "bearer" in match.group(0).lower():
            return f"{prefix}Bearer ***"
        return f"{prefix}***"

    text = NAMED_SECRET_RE.sub(_replace_named_secret, text)
    return EMAIL_RE.sub("[redacted-email]", text)


async def _terminate_timed_out_clone(proc: Any) -> None:
    """Best-effort subprocess cleanup that preserves the original timeout."""
    if getattr(proc, "returncode", None) is not None:
        return

    try:
        proc.kill()
    except ProcessLookupError:
        pass
    except Exception as exc:
        logger.warning(
            "Failed to kill timed-out git clone process: %s",
            _clone_error_text(str(exc), ""),
        )
        return

    try:
        await asyncio.wait_for(
            proc.communicate(),
            timeout=CLONE_DRAIN_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "Failed to drain timed-out git clone process: %s",
            _clone_error_text(str(exc), ""),
        )


async def _clone_repository_async(auth_clone_url: str, clone_target: Path) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth",
            "1",
            "--",
            auth_clone_url,
            str(clone_target),
            env=_git_clone_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "git executable not found; install git to clone repositories"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"failed to start git clone: {_clone_error_text(str(exc), '')}"
        ) from exc
    try:
        _stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        await _terminate_timed_out_clone(proc)
        raise RuntimeError(
            f"git clone timed out after {CLONE_TIMEOUT_SECONDS}s"
        ) from exc

    if proc.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace")
        stdout_text = _stdout.decode("utf-8", errors="replace")
        raise RuntimeError(
            "git clone failed "
            f"(exit {proc.returncode}): {_clone_error_text(stderr_text, stdout_text)}"
        )


def _clone_repository_sync(auth_clone_url: str, clone_target: Path) -> None:
    try:
        completed = subprocess.run(
            ["git", "clone", "--depth", "1", "--", auth_clone_url, str(clone_target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_git_clone_env(),
            timeout=CLONE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "git executable not found; install git to clone repositories"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"failed to start git clone: {_clone_error_text(str(exc), '')}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"git clone timed out after {CLONE_TIMEOUT_SECONDS}s"
        ) from exc

    if completed.returncode != 0:
        raise RuntimeError(
            "git clone failed "
            f"(exit {completed.returncode}): "
            f"{_clone_error_text(completed.stderr, completed.stdout)}"
        )


@dataclass
class ClonedRepository:
    working_dir: Path
    repo_path: Path
    graph: CodebaseGraph

    def list_files(self) -> list[str]:
        files: list[str] = []
        repo_root = self.repo_path.resolve()
        for dirpath, dirnames, filenames in os.walk(repo_root):
            dirnames[:] = sorted(
                dirname for dirname in dirnames if dirname not in SKIP_DIRS
            )
            for filename in sorted(filenames):
                path = Path(dirpath) / filename
                if not path.is_file():
                    continue
                resolved_path = path.resolve()
                if repo_root not in {resolved_path, *resolved_path.parents}:
                    continue
                files.append(path.relative_to(repo_root).as_posix())
        return files

    def read_text(self, relative_path: str, max_chars: int = 6000) -> str:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("relative_path must be a non-empty string")
        if _has_ascii_control(relative_path):
            raise ValueError("relative_path contains an invalid path segment")
        target = (self.repo_path / relative_path).resolve()
        repo_root = self.repo_path.resolve()
        if repo_root not in {target, *target.parents}:
            raise ValueError("Attempted to read a file outside the cloned repository")
        if not target.is_file():
            raise ValueError("Requested path is not a file in the cloned repository")
        if isinstance(max_chars, bool):
            max_chars = 6000
        else:
            try:
                max_chars = int(max_chars)
            except (TypeError, ValueError, OverflowError):
                max_chars = 6000
        if max_chars < 0:
            max_chars = 0
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(max_chars)


@asynccontextmanager
async def cloned_repository(
    clone_url: str,
    token: str | None = None,
):
    """Yield a temporary cloned repository and clean it up afterwards."""
    clone_url = _normalize_clone_url(clone_url)
    auth_clone_url = _build_authenticated_clone_url(clone_url, token)
    graph = CodebaseGraph()
    working_dir = Path(tempfile.mkdtemp(prefix="codey_repo_"))
    clone_target = working_dir / "repo"
    try:
        await _clone_repository_async(auth_clone_url, clone_target)

        nodes, edges = parse_directory(clone_target)
        graph.build_from_nodes_edges(nodes, edges)
        yield ClonedRepository(
            working_dir=working_dir,
            repo_path=clone_target,
            graph=graph,
        )
    finally:
        shutil.rmtree(working_dir, ignore_errors=True)


async def build_graph_from_clone_url(
    clone_url: str,
    token: str | None = None,
) -> CodebaseGraph:
    """Clone a repository asynchronously and return a parsed graph."""
    async with cloned_repository(clone_url, token=token) as repo:
        return repo.graph


def build_graph_from_clone_url_sync(
    clone_url: str,
    token: str | None = None,
) -> CodebaseGraph:
    """Clone a repository synchronously and return a parsed graph."""
    clone_url = _normalize_clone_url(clone_url)
    auth_clone_url = _build_authenticated_clone_url(clone_url, token)
    graph = CodebaseGraph()
    working_dir = Path(tempfile.mkdtemp(prefix="codey_repo_"))
    clone_target = working_dir / "repo"
    try:
        _clone_repository_sync(auth_clone_url, clone_target)

        nodes, edges = parse_directory(clone_target)
        graph.build_from_nodes_edges(nodes, edges)
        return graph
    finally:
        shutil.rmtree(working_dir, ignore_errors=True)
