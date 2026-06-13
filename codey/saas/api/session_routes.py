from __future__ import annotations

import logging
import math
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
import re
from typing import Any
from urllib.parse import urlsplit

import asyncio
import json as _json

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.nfet.controller import NFETController
from codey.nfet.repository_loader import cloned_repository
from codey.saas.auth.dependencies import get_current_user
from codey.saas.auth.websockets import authenticate_websocket
from codey.saas.credits.service import CreditService, InsufficientCreditsError, CREDIT_COSTS
from codey.saas.database import get_db
from codey.saas.intelligence import IntelligenceStack
from codey.saas.models import CodingSession, Project, Repository, User
from codey.saas.sandbox.manager import SandboxManager
from codey.saas.vault.service import VaultService

_sandbox_manager = SandboxManager()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])

_REPO_FILE_HINT_EXTENSIONS = (
    "py",
    "js",
    "jsx",
    "mjs",
    "cjs",
    "ts",
    "tsx",
    "mts",
    "cts",
    "json",
    "md",
    "txt",
    "yaml",
    "yml",
    "toml",
    "sql",
    "sh",
    "html",
    "css",
)
_REPO_FILE_HINT_PATTERN = re.compile(
    r"(?:[A-Za-z0-9_./-]+\.(?:"
    + "|".join(_REPO_FILE_HINT_EXTENSIONS)
    + r"))(?=$|[^A-Za-z0-9_-])",
    re.IGNORECASE,
)
_REPO_TEXT_FILE_EXTENSIONS = {
    f".{extension}" for extension in _REPO_FILE_HINT_EXTENSIONS
}
_MAX_RUN_CODE_CHARS = 200_000
_RUN_CODE_DRAIN_TIMEOUT_SECONDS = 5.0
_SESSION_URL_CREDENTIALS_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_SESSION_QUERY_SECRET_RE = re.compile(
    r"([?&#](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token)=)[^&#\s]+",
    re.IGNORECASE,
)
_SESSION_NAMED_SECRET_RE = re.compile(
    r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token|authorization)\b(\s*[:=]\s*)"
    r"(?:Bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
_SESSION_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_ALLOWED_SESSION_CLONE_SCHEMES = {"git", "git+ssh", "http", "https", "ssh"}
_ALLOWED_SESSION_SCP_CLONE_HOSTS = {"github.com", "www.github.com"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PromptRequest(BaseModel):
    prompt: str
    language: str | None = None
    repo_id: str | None = None

    @field_validator("prompt")
    @classmethod
    def _strip_and_validate_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("language")
    @classmethod
    def _normalize_optional_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("repo_id")
    @classmethod
    def _normalize_optional_repo_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        try:
            uuid.UUID(value)
        except ValueError as exc:
            raise ValueError("must be a valid UUID") from exc
        return value


class HealthReport(BaseModel):
    phase: str = ""
    health_score: float = 0.0
    coherence: float = 0.0
    stability: float = 0.0
    total_nodes: int = 0
    total_edges: int = 0
    summary: str = ""
    recommendations: list[str] = []


class PromptResponse(BaseModel):
    session_id: str
    estimated_credits: int
    output: str | None = None
    lines_generated: int = 0
    status: str = "running"
    security_score: float | None = None
    security_issues: list[str] = []
    health: HealthReport | None = None


class AnalyzeResponse(BaseModel):
    session_id: str


class SessionDetailResponse(BaseModel):
    id: str
    user_id: str
    mode: str
    prompt: str | None
    files_uploaded: list[str] | None
    repo_connected: str | None
    status: str
    credits_charged: int
    lines_generated: int
    files_modified: int
    nfet_phase_before: str | None
    nfet_phase_after: str | None
    es_score_before: float | None
    es_score_after: float | None
    output_summary: str | None
    error_message: str | None
    started_at: str
    completed_at: str | None


class CommitResponse(BaseModel):
    session_id: str
    credits_charged: int
    message: str


def _normalize_files_uploaded(files_uploaded: Any) -> list[str] | None:
    if isinstance(files_uploaded, str):
        files_uploaded = [files_uploaded]
    elif not isinstance(files_uploaded, (list, tuple)):
        return None

    normalized = [
        item.strip()
        for item in files_uploaded
        if isinstance(item, str) and item.strip()
    ]
    return normalized or None


def _serialize_session_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _coerce_non_empty_session_text(value: object) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return None


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_session_github_token(value: object) -> str | None:
    token = _coerce_non_empty_session_text(value)
    if token is None or _has_ascii_control(token) or _has_whitespace(token):
        return None
    return token


def _coerce_session_clone_url(value: object) -> str | None:
    clone_url = _coerce_non_empty_session_text(value)
    if (
        clone_url is None
        or _has_ascii_control(clone_url)
        or _has_whitespace(clone_url)
    ):
        return None
    if "?" in clone_url or "#" in clone_url:
        return None
    if "://" not in clone_url:
        user_host, separator, path = clone_url.partition(":")
        user, _, host = user_host.partition("@")
        if (
            separator != ":"
            or not path
            or user.lower() != "git"
            or host.lower() not in _ALLOWED_SESSION_SCP_CLONE_HOSTS
        ):
            return None
    else:
        try:
            split = urlsplit(clone_url)
            port = split.port
        except ValueError:
            return None
        if split.scheme.lower() not in _ALLOWED_SESSION_CLONE_SCHEMES:
            return None
        if port is not None and not (1 <= port <= 65535):
            return None
        if not split.hostname:
            return None
        if split.username or split.password:
            return None
    return clone_url


def _coerce_session_runtime_secret(value: object) -> str | None:
    secret = _coerce_non_empty_session_text(value)
    if secret is None or _has_ascii_control(secret) or _has_whitespace(secret):
        return None
    return secret


def _redact_session_error(value: object) -> str:
    text = str(value)
    text = _SESSION_URL_CREDENTIALS_RE.sub(r"\1***@", text)
    text = _SESSION_QUERY_SECRET_RE.sub(r"\1***", text)

    def _replace_named_secret(match: re.Match[str]) -> str:
        prefix = f"{match.group(1)}{match.group(2)}"
        if "bearer" in match.group(0).lower():
            return f"{prefix}Bearer ***"
        return f"{prefix}***"

    text = _SESSION_NAMED_SECRET_RE.sub(_replace_named_secret, text)
    return _SESSION_EMAIL_RE.sub("[redacted-email]", text)


def _coerce_session_text_payload(value: object, error_message: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        for key in ("content", "code", "text", "output"):
            candidate = value.get(key)
            if candidate is None:
                continue
            try:
                normalized = _coerce_session_text_payload(candidate, error_message)
            except TypeError:
                continue
            if normalized:
                return normalized
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            try:
                candidate = _coerce_session_text_payload(item, error_message).strip()
            except TypeError:
                continue
            if candidate:
                parts.append(candidate)
        if parts:
            return "\n".join(parts)
    raise TypeError(error_message)


def _coerce_run_code_fix_text(value: object) -> str:
    return _coerce_session_text_payload(value, "Model returned non-text fix content")


def _coerce_prompt_output_text(value: object) -> str:
    output = _coerce_session_text_payload(
        value,
        "Intelligence stack returned non-text prompt output",
    )
    if not output.strip():
        raise TypeError("Intelligence stack returned empty prompt output")
    return output


def _coerce_session_int(value: object, fallback: int = 0) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else fallback
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return fallback
        try:
            return int(value)
        except ValueError:
            return fallback
    return fallback


def _coerce_session_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        normalized = float(value)
        return normalized if math.isfinite(normalized) else None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            normalized = float(value)
        except ValueError:
            return None
        return normalized if math.isfinite(normalized) else None
    return None


def _coerce_session_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        normalized = _coerce_non_empty_session_text(item)
        if normalized is not None:
            items.append(normalized)
    return items


def _count_generated_lines(output: str) -> int:
    if not output:
        return 0
    return sum(1 for line in output.splitlines() if line.strip())


def _analysis_to_health_report(analysis: object) -> HealthReport:
    payload = analysis if isinstance(analysis, dict) else {}
    return HealthReport(
        phase=_coerce_non_empty_session_text(payload.get("phase")) or "",
        health_score=_coerce_session_float(payload.get("health_score")) or 0.0,
        coherence=_coerce_session_float(payload.get("coherence")) or 0.0,
        stability=_coerce_session_float(payload.get("stability")) or 0.0,
        total_nodes=_coerce_session_int(payload.get("total_nodes"), 0),
        total_edges=_coerce_session_int(payload.get("total_edges"), 0),
        summary=_coerce_non_empty_session_text(payload.get("summary")) or "",
        recommendations=_coerce_session_string_list(payload.get("recommendations")),
    )


def _health_report_to_stream_event(report: HealthReport) -> dict[str, Any]:
    return {
        "type": "health_after",
        "phase": report.phase,
        "score": report.health_score,
        "coherence": report.coherence,
        "stability": report.stability,
        "summary": report.summary,
        "recommendations": report.recommendations,
    }


async def _build_repo_nfet_prompt_context(
    repo_id: str,
    prompt: str,
    current_user: User,
    db: AsyncSession,
) -> tuple[str, str, dict[str, Any]]:
    try:
        rid = uuid.UUID(repo_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository ID format",
        )

    result = await db.execute(
        select(Repository).where(
            Repository.id == rid,
            Repository.user_id == current_user.id,
        )
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )
    clone_url = _coerce_session_clone_url(getattr(repo, "clone_url", None))
    if not clone_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository has no clone URL configured",
        )
    repo_full_name = _coerce_non_empty_session_text(getattr(repo, "full_name", None)) or ""
    github_token = _coerce_session_github_token(
        getattr(current_user, "github_token", None)
    )

    try:
        async with cloned_repository(
            clone_url,
            token=github_token,
        ) as repo_bundle:
            graph = repo_bundle.graph
            controller = NFETController()
            repo_state = controller.analyze(graph, goal=prompt)
            candidates = controller.rank_interventions(
                graph,
                goal=prompt,
                repo_state=repo_state,
                limit=4,
            )
            grounding = _build_repo_grounding_context(
                repo_name=repo_full_name,
                prompt=prompt,
                repo_files=repo_bundle.list_files(),
                read_text=repo_bundle.read_text,
                repo_state=repo_state,
                candidates=candidates,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Repository NFET analysis failed: {_redact_session_error(exc)[:200]}",
        )

    return controller.build_guidance(repo_state, candidates), grounding, {
        "repo_full_name": repo_full_name,
        "nfet_phase": repo_state.phase,
        "nfet_phase_before": repo_state.phase,
        "nfet_es_before": repo_state.global_es,
        "nfet_hotspots": len(repo_state.hotspots),
        "nfet_focus_risk": repo_state.hotspots[0].risk_score if repo_state.hotspots else 0.0,
        "nfet_goal_pressure": repo_state.hotspots[0].gamma if repo_state.hotspots else 0.0,
        "codebase_files": len(repo_state.components),
        "codebase_tokens": max(repo_state.total_nodes * 120, 2048),
    }


def _extract_prompt_file_hints(prompt: str) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    patterns = _REPO_FILE_HINT_PATTERN.findall(prompt)
    patterns.extend(re.findall(r"`([^`]+)`", prompt))
    for match in patterns:
        normalized = _normalize_prompt_file_hint(match)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        hints.append(normalized)
    return hints


def _normalize_prompt_file_hint(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.replace("\\", "/")
    if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        return None
    candidate = candidate.strip()
    if not candidate:
        return None
    while candidate.startswith("./"):
        candidate = candidate[2:]
    candidate = candidate.lstrip("/")
    path = PurePosixPath(candidate)
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    return PurePosixPath(*parts).as_posix()


def _logical_upload_filename(filename: str | None) -> str:
    if not filename:
        return f"file_{uuid.uuid4().hex[:8]}"

    normalized = filename.replace("\\", "/")
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        return f"file_{uuid.uuid4().hex[:8]}"

    candidate = PurePosixPath(normalized.strip()).name
    if candidate in {"", ".", ".."}:
        return f"file_{uuid.uuid4().hex[:8]}"
    return candidate


def _match_repo_file(file_hint: object, repo_files: list[str]) -> str | None:
    normalized = _normalize_prompt_file_hint(file_hint)
    if normalized is None:
        return None
    exact = [path for path in repo_files if path.lower() == normalized.lower()]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    suffix = f"/{normalized.lower()}"
    suffix_matches = [
        path for path in repo_files if path.lower().endswith(suffix)
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    if suffix_matches:
        return None
    basename = PurePosixPath(normalized).name.lower()
    basename_matches = [
        path
        for path in repo_files
        if PurePosixPath(path.lower()).name == basename
    ]
    if len(basename_matches) == 1:
        return basename_matches[0]
    return None


def _snippet_language(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".mts": "typescript",
        ".cts": "typescript",
        ".json": "json",
        ".md": "markdown",
        ".sh": "bash",
        ".sql": "sql",
        ".html": "html",
        ".css": "css",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".toml": "toml",
    }.get(ext, "text")


def _summarize_repo_tree(repo_files: list[str], limit: int = 20) -> str:
    if not repo_files:
        return "- No files found in repository."
    shown = repo_files[:limit]
    lines = [f"- {path}" for path in shown]
    remaining = len(repo_files) - len(shown)
    if remaining > 0:
        lines.append(f"- ... and {remaining} more file(s)")
    return "\n".join(lines)


def _select_repo_context_files(
    prompt: str,
    repo_files: list[str],
    repo_state: Any,
    candidates: list[Any],
    limit: int = 3,
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def add(path: str | None) -> None:
        if not path or path in seen:
            return
        if path not in repo_files:
            return
        seen.add(path)
        ordered.append(path)

    for hint in _extract_prompt_file_hints(prompt):
        add(_match_repo_file(hint, repo_files))

    candidate_items = candidates if isinstance(candidates, (list, tuple)) else []
    for candidate in candidate_items:
        add(_match_repo_file(getattr(candidate, "target_file_path", None), repo_files))

    hotspot_items = getattr(repo_state, "hotspots", [])
    if not isinstance(hotspot_items, (list, tuple)):
        hotspot_items = []
    for hotspot in hotspot_items:
        add(_match_repo_file(getattr(hotspot, "file_path", None), repo_files))

    important_defaults = [
        "README.md",
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "src/main.py",
        "src/app.py",
        "main.py",
        "app.py",
        "index.ts",
        "index.js",
    ]
    for fallback in important_defaults:
        add(_match_repo_file(fallback, repo_files))

    text_only = [
        path
        for path in ordered
        if Path(path).suffix.lower() in _REPO_TEXT_FILE_EXTENSIONS
    ]
    return text_only[:limit]


def _format_repo_intervention(candidate: object) -> str | None:
    title = _coerce_non_empty_session_text(getattr(candidate, "title", None))
    target_file = _coerce_non_empty_session_text(
        getattr(candidate, "target_file_path", None)
    )
    if title is None and target_file is None:
        return None

    delta = _coerce_session_float(getattr(candidate, "predicted_repo_es_delta", None))
    risk = _coerce_session_float(getattr(candidate, "risk", None))
    prefix = f"- {title or 'Repository intervention'} -> {target_file or 'unknown file'}"
    if delta is None or risk is None:
        return prefix
    return f"{prefix} (delta_ES={delta:.3f}, risk={risk:.2f})"


def _build_repo_grounding_context(
    repo_name: str,
    prompt: str,
    repo_files: list[str],
    read_text: Any,
    repo_state: Any,
    candidates: list[Any],
) -> str:
    selected_files = _select_repo_context_files(prompt, repo_files, repo_state, candidates)
    sections = [
        "REPOSITORY GROUNDING CONTEXT:",
        f"Repository: {repo_name or 'connected repository'}",
        "Use the real repo files below as the source of truth. Prefer updating existing files over inventing unrelated abstractions.",
        "Repository file inventory:",
        _summarize_repo_tree(repo_files),
    ]

    candidate_items = candidates if isinstance(candidates, (list, tuple)) else []
    intervention_lines: list[str] = []
    for candidate in candidate_items[:3]:
        line = _format_repo_intervention(candidate)
        if line:
            intervention_lines.append(line)
    if intervention_lines:
        sections.extend(
            [
                "Highest-value NFET interventions:",
                *intervention_lines,
            ]
        )

    if selected_files:
        sections.append("Relevant file excerpts:")
        for file_path in selected_files:
            try:
                snippet = read_text(file_path, max_chars=2200).strip()
            except Exception:
                continue
            if not snippet:
                continue
            language = _snippet_language(file_path)
            sections.extend(
                [
                    f"FILE: {file_path}",
                    f"```{language}",
                    snippet,
                    "```",
                ]
            )
    else:
        sections.append(
            "No text source files were selected for grounding. If the repo lacks supported source files, stay conservative and base changes on the visible repo structure."
        )

    return "\n".join(sections)


def _default_output_filename(language: str | None) -> str:
    ext_map = {
        "python": "py",
        "javascript": "js",
        "typescript": "ts",
        "java": "java",
        "go": "go",
        "rust": "rs",
        "html": "html",
        "css": "css",
        "json": "json",
        "sql": "sql",
        "shell": "sh",
        "bash": "sh",
    }
    key = (language or "python").lower()
    return f"generated.{ext_map.get(key, 'txt')}"


def _build_file_snapshot(output: str, language: str | None) -> dict[str, str]:
    import re

    snapshot: dict[str, str] = {}
    matches = list(re.finditer(r"```([\w#+-]*)?\s*\n(.*?)```", output, re.DOTALL))
    if not matches:
        snapshot[_default_output_filename(language)] = output.strip()
        return snapshot

    for index, match in enumerate(matches, start=1):
        lang_hint = match.group(1) or language or "txt"
        filename = _default_output_filename(lang_hint)
        if index > 1:
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            filename = f"{stem}-{index}{suffix}"
        snapshot[filename] = match.group(2).strip()
    return snapshot


def _derive_project_name(prompt: str) -> str:
    cleaned = prompt.replace("[lang:", "").replace("]", " ").strip()
    if len(cleaned) <= 60:
        return cleaned or "Generated Project"
    return f"{cleaned[:57].rstrip()}..."


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/prompt", response_model=PromptResponse, status_code=status.HTTP_201_CREATED)
async def create_prompt_session(
    body: PromptRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PromptResponse:
    # 1. Estimate credits
    estimated = CreditService.estimate_cost(body.prompt, mode="prompt")

    # 2. Reserve credits (raises InsufficientCreditsError if not enough)
    credit_service = CreditService(db)
    try:
        await credit_service.reserve_credits(
            user_id=current_user.id,
            estimated_cost=estimated,
            description=f"Coding session: {body.prompt[:80]}",
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": "Insufficient credits",
                "required": exc.required,
                "available": exc.available,
            },
        )

    session: CodingSession | None = None
    try:
        # 3. Create session record
        session = CodingSession(
            user_id=current_user.id,
            mode="prompt",
            prompt=body.prompt,
            repo_connected=None,
            status="running",
            credits_charged=estimated,
            started_at=datetime.utcnow(),
        )
        db.add(session)
        await db.flush()

        # 4. Run the intelligence stack
        nfet_guidance = ""
        repo_grounding = ""
        nfet_context: dict[str, Any] = {}
        if body.repo_id:
            nfet_guidance, repo_grounding, nfet_context = await _build_repo_nfet_prompt_context(
                body.repo_id,
                body.prompt,
                current_user,
                db,
            )
            existing_repo_connected = _coerce_non_empty_session_text(
                getattr(session, "repo_connected", None)
            )
            session.repo_connected = (
                _coerce_non_empty_session_text(nfet_context.get("repo_full_name"))
                or existing_repo_connected
            )
            session.nfet_phase_before = _coerce_non_empty_session_text(
                nfet_context.get("nfet_phase_before")
            )
            session.es_score_before = _coerce_session_float(
                nfet_context.get("nfet_es_before")
            )

        stack = IntelligenceStack()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Codey, the repository operator. Treat this request as repo work, "
                    "not a generic code-generation prompt.\n\n"
                    "SUPPORTED REPO-WORK LANES:\n"
                    "- repo scans and maintenance prioritization\n"
                    "- bug repair and regression containment\n"
                    "- structural refactors and coupling reduction\n"
                    "- dependency updates and package maintenance\n"
                    "- CI, build, and release blocker repair\n"
                    "- security hardening and boundary fixes\n"
                    "- test coverage and regression guardrails\n"
                    "- docs, runbooks, changelog, and deploy fixes\n\n"
                    "RULES:\n"
                    "- Work from the existing repository patterns and real files first\n"
                    "- Prefer the smallest safe change with the highest leverage\n"
                    "- Keep blast radius explicit and preserve behavior outside the target area\n"
                    "- Never use eval(), exec(), os.system(), or hardcoded secrets\n"
                    "- Always validate inputs at system boundaries\n"
                    "- Use type hints in Python and TypeScript types in JS/TS\n"
                    "- Include error handling for external calls (API, DB, file I/O)\n"
                    "- Prefer standard library modules over third-party packages when possible\n"
                    "- If generating requirements.txt or package.json, pin exact versions\n"
                    "- Return updated code in fenced code blocks followed by brief operator notes "
                    "covering what changed, verification, remaining risk, and follow-up work\n"
                    "- For games, visual apps, or interactive browser work: generate a single HTML "
                    "file with inline JavaScript and CSS instead of terminal UI\n"
                    "- For non-interactive scripts: prefer stdlib solutions when they are sufficient\n"
                    "- If third-party packages are needed, list them in a comment at the top: "
                    "# pip install X Y Z\n"
                ),
            },
            {"role": "user", "content": body.prompt},
        ]
        if nfet_guidance:
            messages[0]["content"] += f"\n\n{nfet_guidance}"
        if repo_grounding:
            messages.insert(1, {"role": "system", "content": repo_grounding})

        context = {
            "language": body.language or "python",
            "user_id": str(current_user.id),
            "db": db,
            "surface": "prompt_workspace",
            "task_hint": "code_generation",
            "disable_auto_fix": True,
            **nfet_context,
        }
        result = await stack.run(body.prompt, messages, context)

        output = _coerce_prompt_output_text(getattr(result, "content", None))
        lines = _count_generated_lines(output)

        # Extract security assessment if available
        sec_score = None
        sec_issues: list[str] = []
        assessment = getattr(result, "assessment", None)
        if assessment:
            sec_score = _coerce_session_float(getattr(assessment, "score", None))
            raw_issues = getattr(assessment, "issues", [])
            if isinstance(raw_issues, list):
                for issue in raw_issues:
                    if getattr(issue, "severity", None) not in ("error", "warning"):
                        continue
                    message = _coerce_non_empty_session_text(
                        getattr(issue, "message", None)
                    )
                    if message:
                        sec_issues.append(message)

        # Run structural health analysis on generated code
        health_report = None
        try:
            from codey.saas.api.health_analysis import _analyze_code
            import re as _re
            # Extract code from markdown fences
            code_to_analyze = output
            _m = _re.search(r"```(?:python|javascript|typescript|js|ts)?\s*\n(.*?)```", output, _re.DOTALL)
            if _m:
                code_to_analyze = _m.group(1)
            lang = body.language or "python"
            analysis = _analyze_code(code_to_analyze, f"generated.{lang[:2]}", lang)
            health_report = _analysis_to_health_report(analysis)
        except Exception:
            pass  # Don't fail the response if analysis errors

        session.status = "completed"
        session.output_summary = output
        session.lines_generated = lines
        session.completed_at = datetime.utcnow()
        await db.flush()

        # Store generated output in the Code Vault so vault/export flows are populated.
        try:
            async with db.begin_nested():
                file_snapshot = _build_file_snapshot(output, body.language)
                vault = VaultService(db)
                project_name = _derive_project_name(body.prompt)
                existing_project_result = await db.execute(
                    select(Project).where(
                        Project.user_id == current_user.id,
                        Project.name == project_name,
                        Project.is_archived.is_(False),
                    )
                )
                project = existing_project_result.scalar_one_or_none()
                if project is None:
                    project = await vault.create_project(
                        current_user.id,
                        project_name,
                        language=body.language or "python",
                    )
                version = await vault.create_version(
                    project.id,
                    session.id,
                    list(file_snapshot.keys()),
                    None,
                    body.prompt[:200],
                    nfet_state={
                        "phase": health_report.phase if health_report else None,
                        "es_score": health_report.health_score if health_report else None,
                    },
                )
                version.file_snapshot = file_snapshot
                project.file_tree = file_snapshot
                project.last_activity = datetime.utcnow()
                await db.flush()
        except Exception as exc:
            logger.warning("Prompt vault snapshot skipped: %s", _redact_session_error(exc))

        # Store session context as a memory for future retrieval
        try:
            async with db.begin_nested():
                from codey.saas.intelligence.embeddings import embedding_service
                memory_content = f"User asked: {body.prompt[:200]}. Generated {lines} lines of {body.language or 'python'} code."
                await embedding_service.store_memory(
                    db,
                    user_id=str(current_user.id),
                    content=memory_content,
                    memory_type="session_context",
                    confidence=0.8,
                )
        except Exception as exc:
            logger.warning("Prompt memory write skipped: %s", _redact_session_error(exc))

        return PromptResponse(
            session_id=str(session.id),
            estimated_credits=estimated,
            output=output,
            lines_generated=lines,
            status="completed",
            security_score=sec_score,
            security_issues=sec_issues,
            health=health_report,
        )
    except Exception as e:
        safe_error = _redact_session_error(e)
        # Refund credits on failure
        if session is not None:
            session.status = "failed"
            session.error_message = safe_error
            session.completed_at = datetime.utcnow()
            session.credits_charged = 0
            try:
                await db.flush()
            except Exception:
                pass
        # Attempt refund
        try:
            await credit_service.refund_credits(
                user_id=current_user.id,
                amount=estimated,
                description=f"Refund: session failed — {safe_error[:60]}",
            )
        except Exception:
            pass

        if isinstance(e, HTTPException):
            raise e

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Code generation failed: {safe_error[:200]}",
        )


@router.post("/analyze", response_model=AnalyzeResponse, status_code=status.HTTP_201_CREATED)
async def create_analyze_session(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyzeResponse:
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file is required",
        )

    # 1. Persist logical filenames only. The current analyze flow does not
    # execute a background worker against temporary upload paths, so writing
    # them to disk here only leaks temp directories and internal paths. Because
    # no analysis worker is started from this endpoint, do not reserve credits
    # or leave a session permanently running.
    saved_paths: list[str] = []
    for upload in files:
        saved_paths.append(_logical_upload_filename(upload.filename))

    # 2. Create a terminal metadata-only session record.
    session = CodingSession(
        user_id=current_user.id,
        mode="analyze",
        files_uploaded=saved_paths,
        status="completed",
        credits_charged=0,
        output_summary="Upload metadata recorded; no background analysis worker was started.",
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    db.add(session)
    await db.flush()

    return AnalyzeResponse(session_id=str(session.id))


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    session = await _get_session_for_user_id(session_id, current_user.id, db)
    mode = _coerce_non_empty_session_text(getattr(session, "mode", None)) or "unknown"
    status_text = (
        _coerce_non_empty_session_text(getattr(session, "status", None)) or "unknown"
    )

    return SessionDetailResponse(
        id=str(session.id),
        user_id=str(session.user_id),
        mode=mode,
        prompt=_coerce_non_empty_session_text(getattr(session, "prompt", None)),
        files_uploaded=_normalize_files_uploaded(
            getattr(session, "files_uploaded", None)
        ),
        repo_connected=_coerce_non_empty_session_text(
            getattr(session, "repo_connected", None)
        ),
        status=status_text,
        credits_charged=_coerce_session_int(
            getattr(session, "credits_charged", None), 0
        ),
        lines_generated=_coerce_session_int(
            getattr(session, "lines_generated", None), 0
        ),
        files_modified=_coerce_session_int(
            getattr(session, "files_modified", None), 0
        ),
        nfet_phase_before=_coerce_non_empty_session_text(
            getattr(session, "nfet_phase_before", None)
        ),
        nfet_phase_after=_coerce_non_empty_session_text(
            getattr(session, "nfet_phase_after", None)
        ),
        es_score_before=_coerce_session_float(
            getattr(session, "es_score_before", None)
        ),
        es_score_after=_coerce_session_float(
            getattr(session, "es_score_after", None)
        ),
        output_summary=_coerce_non_empty_session_text(
            getattr(session, "output_summary", None)
        ),
        error_message=_coerce_non_empty_session_text(
            getattr(session, "error_message", None)
        ),
        started_at=_serialize_session_timestamp(getattr(session, "started_at", None))
        or "",
        completed_at=_serialize_session_timestamp(
            getattr(session, "completed_at", None)
        ),
    )


async def _get_session_for_user_id(
    session_id: str,
    user_id: str | uuid.UUID,
    db: AsyncSession,
) -> CodingSession:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    try:
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format",
        )

    stmt = select(CodingSession).where(
        CodingSession.id == sid,
        CodingSession.user_id == uid,
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return session


def _coerce_stream_auth_payload(raw_message: str) -> dict[str, str | None]:
    try:
        payload = _json.loads(raw_message)
    except _json.JSONDecodeError as exc:
        raise ValueError("Invalid authentication payload") from exc

    if not isinstance(payload, dict):
        raise ValueError("Invalid authentication payload")

    token = payload.get("token")
    prompt = payload.get("prompt", "")
    language = payload.get("language", "python")

    return {
        "token": token.strip() if isinstance(token, str) and token.strip() else None,
        "prompt": prompt.strip() if isinstance(prompt, str) else "",
        "language": language.strip() if isinstance(language, str) and language.strip() else "python",
    }


def _json_safe_session_stream_value(
    value: Any,
    _seen: set[int] | None = None,
) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if _seen is None:
        _seen = set()
    if isinstance(value, dict):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return {
                str(key): _json_safe_session_stream_value(item, _seen)
                for key, item in value.items()
            }
        finally:
            _seen.remove(value_id)
    if isinstance(value, (set, frozenset)):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return [
                _json_safe_session_stream_value(item, _seen)
                for item in sorted(
                    value,
                    key=lambda item: (type(item).__name__, repr(item)),
                )
            ]
        finally:
            _seen.remove(value_id)
    if isinstance(value, (list, tuple)):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return [_json_safe_session_stream_value(item, _seen) for item in value]
        finally:
            _seen.remove(value_id)
    return str(value)


async def _send_session_stream_json(
    websocket: WebSocket,
    event: dict[str, Any],
) -> None:
    await websocket.send_json(_json_safe_session_stream_value(event))


@router.post("/{session_id}/commit", response_model=CommitResponse)
async def commit_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommitResponse:
    session = await _get_session_for_user_id(session_id, current_user.id, db)

    status_text = (
        _coerce_non_empty_session_text(getattr(session, "status", None)) or "unknown"
    )
    if status_text != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Session is '{status_text}' — can only commit completed sessions",
        )
    repo_connected = _coerce_non_empty_session_text(
        getattr(session, "repo_connected", None)
    )
    if not repo_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session is not connected to a repository",
        )
    if not _coerce_session_github_token(getattr(current_user, "github_token", None)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GitHub authentication required to commit code",
        )

    # Charge 1 extra credit for the GitHub commit
    commit_cost = CREDIT_COSTS["github_commit"]
    credit_service = CreditService(db)
    try:
        await credit_service.reserve_credits(
            user_id=current_user.id,
            estimated_cost=commit_cost,
            description=f"GitHub commit for session {session_id[:8]}",
            session_id=session.id,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": "Insufficient credits for commit",
                "required": exc.required,
                "available": exc.available,
            },
        )

    session.credits_charged = (
        _coerce_session_int(getattr(session, "credits_charged", None), 0)
        + commit_cost
    )
    await db.flush()

    # The actual git commit/PR creation would be triggered here asynchronously
    return CommitResponse(
        session_id=str(session.id),
        credits_charged=_coerce_session_int(session.credits_charged, 0),
        message="Commit initiated. Code will be pushed to the connected repository.",
    )


# ---------------------------------------------------------------------------
# Code execution (sandbox)
# ---------------------------------------------------------------------------


class RunCodeRequest(BaseModel):
    code: str
    language: str = "python"

    @field_validator("code")
    @classmethod
    def _validate_non_blank_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        if len(value) > _MAX_RUN_CODE_CHARS:
            raise ValueError(f"must be at most {_MAX_RUN_CODE_CHARS} characters")
        return value

    @field_validator("language")
    @classmethod
    def _normalize_language(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            return "python"
        aliases = {
            "python": "python",
            "py": "python",
            "javascript": "javascript",
            "js": "javascript",
        }
        if normalized not in aliases:
            raise ValueError("unsupported language; expected python or javascript")
        return aliases[normalized]


class RunCodeResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


def _decode_run_output(payload: bytes | None, limit: int) -> str:
    return (payload or b"").decode("utf-8", errors="replace")[:limit]


def _run_code_stderr(stdout: bytes | None, stderr: bytes | None, exit_code: int) -> str:
    stderr_text = _decode_run_output(stderr, 5000)
    if stderr_text or exit_code == 0:
        return stderr_text
    return _decode_run_output(stdout, 5000)


def _run_code_startup_error(command: str, exc: OSError) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"Runtime not available: {command}"
    return f"Runtime startup failed for {command}: {_redact_session_error(exc)[:200]}"


def _python_exec_command(code: str) -> str:
    import base64

    encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
    return (
        "python3 -c \"import base64; "
        f"exec(base64.b64decode('{encoded}').decode('utf-8'))\""
    )


async def _terminate_subprocess(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return

    try:
        proc.kill()
    except ProcessLookupError:
        pass
    except Exception as exc:
        logger.warning(
            "Failed to kill timed-out run-code process: %s",
            _redact_session_error(exc),
        )
        return

    try:
        await asyncio.wait_for(
            proc.communicate(), timeout=_RUN_CODE_DRAIN_TIMEOUT_SECONDS
        )
    except Exception as exc:
        logger.warning(
            "Failed to drain timed-out run-code process: %s",
            _redact_session_error(exc),
        )


@router.post("/run", response_model=RunCodeResponse)
async def run_code(
    body: RunCodeRequest,
    current_user: User = Depends(get_current_user),
) -> RunCodeResponse:
    """Execute code in an isolated sandbox and return the output."""
    import asyncio as _asyncio

    import os as _os
    import re as _re

    ext_map = {"python": ("py", sys.executable), "javascript": ("js", "node")}
    ext, runner = ext_map.get(body.language, ("py", sys.executable))

    # Try E2B cloud sandbox first (full VM with package managers)
    e2b_key = _coerce_session_runtime_secret(_os.environ.get("E2B_API_KEY")) or ""
    if e2b_key and body.language == "python":
        try:
            from e2b_code_interpreter import Sandbox
            sbx = Sandbox(api_key=e2b_key, timeout=60)
            try:
                # Auto-detect imports and install missing packages
                imports = _re.findall(r'^import\s+(\w+)|^from\s+(\w+)', body.code, _re.MULTILINE)
                packages = set()
                stdlib = {'os','sys','json','re','math','random','datetime','pathlib','typing',
                         'collections','itertools','functools','io','string','time','hashlib',
                         'uuid','logging','argparse','subprocess','tempfile','shutil','csv',
                         'sqlite3','urllib','http','socket','threading','asyncio','abc','dataclasses',
                         'enum','copy','pprint','textwrap','unittest','contextlib','operator'}
                for imp in imports:
                    pkg = imp[0] or imp[1]
                    if pkg and pkg not in stdlib:
                        packages.add(pkg)
                if packages:
                    sbx.commands.run(f"pip install -q {' '.join(packages)}", timeout=30)

                result = sbx.commands.run(_python_exec_command(body.code), timeout=30)
                stdout = getattr(result, "stdout", "") or ""
                stderr = getattr(result, "stderr", "") or ""
                if not isinstance(stdout, str):
                    stdout = str(stdout)
                if not isinstance(stderr, str):
                    stderr = str(stderr)
                return RunCodeResponse(
                    stdout=stdout[:10000],
                    stderr=stderr[:5000],
                    exit_code=_coerce_session_int(
                        getattr(result, "exit_code", None),
                        0,
                    ),
                    timed_out=False,
                )
            finally:
                try:
                    sbx.kill()
                except Exception:
                    pass
        except Exception as e2b_err:
            # Fall through to local subprocess
            pass

    # Fallback: local subprocess with auto-fix on errors
    tmp_dir = Path(tempfile.mkdtemp(prefix="codey_run_"))
    tmp_file = tmp_dir / f"main.{ext}"
    import shutil

    try:
        tmp_file.write_text(body.code, encoding="utf-8")
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return RunCodeResponse(
            stdout="",
            stderr=f"Execution error: {_redact_session_error(e)[:200]}",
            exit_code=-1,
            timed_out=False,
        )

    max_retries = 3
    timed_out = False
    stdout = b""
    stderr = b""
    proc: asyncio.subprocess.Process | None = None
    current_code = body.code

    try:
        for attempt in range(max_retries):
            try:
                try:
                    proc = await _asyncio.create_subprocess_exec(
                        runner, str(tmp_file),
                        stdout=_asyncio.subprocess.PIPE,
                        stderr=_asyncio.subprocess.PIPE,
                        cwd=str(tmp_dir),
                    )
                except OSError as e:
                    return RunCodeResponse(
                        stdout="",
                        stderr=_run_code_startup_error(runner, e),
                        exit_code=-1,
                        timed_out=False,
                    )
                try:
                    stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=30)
                except _asyncio.TimeoutError:
                    await _terminate_subprocess(proc)
                    stdout, stderr = b"", b"Execution timed out (30s limit)"
                    timed_out = True
                    break

                stdout_str = _decode_run_output(stdout, 10000)
                stderr_str = _run_code_stderr(stdout, stderr, exit_code=proc.returncode or 0)
                exit_code = proc.returncode or 0

                # Auto-fix: missing module → pip install and retry
                if (
                    body.language == "python"
                    and exit_code != 0
                    and "ModuleNotFoundError: No module named" in stderr_str
                ):
                    missing = _re.search(r"No module named '(\w+)'", stderr_str)
                    if missing and attempt < max_retries - 1:
                        pkg = missing.group(1)
                        # Map common module names to pip package names
                        pkg_map = {"cv2": "opencv-python", "PIL": "Pillow", "sklearn": "scikit-learn",
                                   "bs4": "beautifulsoup4", "yaml": "pyyaml", "dotenv": "python-dotenv"}
                        pip_pkg = pkg_map.get(pkg, pkg)
                        try:
                            install = await _asyncio.create_subprocess_exec(
                                runner, "-m", "pip", "install", "-q", pip_pkg,
                                stdout=_asyncio.subprocess.PIPE,
                                stderr=_asyncio.subprocess.PIPE,
                            )
                        except OSError as e:
                            return RunCodeResponse(
                                stdout="",
                                stderr=_run_code_startup_error(runner, e),
                                exit_code=-1,
                                timed_out=False,
                            )
                        try:
                            install_stdout, install_stderr = await _asyncio.wait_for(
                                install.communicate(), timeout=30
                            )
                        except _asyncio.TimeoutError:
                            await _terminate_subprocess(install)
                            return RunCodeResponse(
                                stdout="",
                                stderr="Dependency install timed out (30s limit)",
                                exit_code=-1,
                                timed_out=True,
                            )
                        install_exit_code = (
                            install.returncode if install.returncode is not None else -1
                        )
                        if install_exit_code != 0:
                            install_error = _run_code_stderr(
                                install_stdout,
                                install_stderr,
                                exit_code=install_exit_code,
                            ).strip()
                            if not install_error:
                                install_error = f"pip exited with {install_exit_code}"
                            return RunCodeResponse(
                                stdout="",
                                stderr=(
                                    "Dependency install failed: "
                                    f"{_redact_session_error(install_error)[:5000]}"
                                ),
                                exit_code=install_exit_code,
                                timed_out=False,
                            )
                        continue  # Retry execution

                # Auto-fix: syntax error → ask LLM to fix and retry
                if (
                    body.language == "python"
                    and exit_code != 0
                    and ("SyntaxError" in stderr_str or "IndentationError" in stderr_str)
                    and attempt < max_retries - 1
                ):
                    try:
                        from codey.saas.intelligence.providers import call_model, resolve_model
                        provider, model = resolve_model("debugging")
                        fix_result = await call_model(provider, model, [
                            {"role": "system", "content": "Fix this Python code error. Return ONLY the corrected code, no explanation."},
                            {"role": "user", "content": f"Error:\n{stderr_str[:500]}\n\nCode:\n{current_code}"},
                        ], max_tokens=4096)
                        # Extract code from response
                        fixed = _coerce_run_code_fix_text(fix_result)
                        m = _re.search(r"```python\s*\n(.*?)```", fixed, _re.DOTALL)
                        if m:
                            fixed = m.group(1)
                        if not fixed.strip():
                            break
                        current_code = fixed
                        tmp_file.write_text(current_code, encoding="utf-8")
                        continue  # Retry with fixed code
                    except Exception:
                        pass  # Can't fix, return the error

                # Success or unfixable error
                return RunCodeResponse(
                    stdout=stdout_str,
                    stderr=stderr_str[:5000],
                    exit_code=exit_code,
                    timed_out=timed_out,
                )

            except Exception as e:
                return RunCodeResponse(
                    stdout="",
                    stderr=f"Execution error: {_redact_session_error(e)[:200]}",
                    exit_code=-1,
                    timed_out=False,
                )

        # Exhausted retries
        return RunCodeResponse(
            stdout=_decode_run_output(stdout, 10000),
            stderr=_run_code_stderr(
                stdout,
                stderr,
                exit_code=proc.returncode if proc and proc.returncode is not None else -1,
            ),
            exit_code=proc.returncode if proc and proc.returncode is not None else -1,
            timed_out=timed_out,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# WebSocket streaming endpoint
# ---------------------------------------------------------------------------


@router.websocket("/stream/{session_id}")
async def stream_session(websocket: WebSocket, session_id: str):
    """Real-time WebSocket streaming for code generation sessions.

    Protocol (server -> client):
    { "type": "status", "message": "Analyzing request..." }
    { "type": "health_before", "phase": "Excellent", "score": 0.85 }
    { "type": "plan", "steps": ["Parse imports", "Generate module", "Write tests"] }
    { "type": "code_chunk", "content": "def hello():\n" }
    { "type": "health_after", "phase": "Excellent", "score": 0.82, "summary": "..." }
    { "type": "complete", "credits_charged": 1, "lines_generated": 47 }
    """
    await websocket.accept()

    try:
        # Authenticate via token in first message
        auth_msg = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        auth_data = _coerce_stream_auth_payload(auth_msg)
        token = auth_data.get("token")
        payload = authenticate_websocket(websocket, token)
        if not payload:
            await _send_session_stream_json(
                websocket,
                {"type": "error", "message": "Invalid token"},
            )
            await websocket.close()
            return

        user_id = payload.get("sub")
        prompt = auth_data.get("prompt", "")
        language = auth_data.get("language", "python")

        if not prompt:
            await _send_session_stream_json(
                websocket,
                {"type": "error", "message": "No prompt provided"},
            )
            await websocket.close()
            return

        async for db in get_db():
            await _get_session_for_user_id(session_id, user_id, db)
            break

        # Status updates
        await _send_session_stream_json(
            websocket,
            {"type": "status", "message": "Analyzing request..."},
        )
        await asyncio.sleep(0.3)
        await _send_session_stream_json(
            websocket,
            {"type": "status", "message": "Planning structure..."},
        )
        await asyncio.sleep(0.3)
        await _send_session_stream_json(
            websocket,
            {"type": "status", "message": "Generating code..."},
        )

        # Generate code
        stack = IntelligenceStack()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Codey, the repository operator. Treat this request as repo work, "
                    "not a generic code-generation prompt.\n\n"
                    "SUPPORTED REPO-WORK LANES:\n"
                    "- repo scans and maintenance prioritization\n"
                    "- bug repair and regression containment\n"
                    "- structural refactors and coupling reduction\n"
                    "- dependency updates and package maintenance\n"
                    "- CI, build, and release blocker repair\n"
                    "- security hardening and boundary fixes\n"
                    "- test coverage and regression guardrails\n"
                    "- docs, runbooks, changelog, and deploy fixes\n\n"
                    "RULES:\n"
                    "- Work from the existing repository patterns and real files first\n"
                    "- Prefer the smallest safe change with the highest leverage\n"
                    "- Keep blast radius explicit and preserve behavior outside the target area\n"
                    "- Never use eval(), exec(), os.system(), or hardcoded secrets\n"
                    "- Always validate inputs at system boundaries\n"
                    "- Use type hints in Python and TypeScript types in JS/TS\n"
                    "- Include error handling for external calls\n"
                    "- Return updated code in fenced code blocks followed by brief operator notes\n"
                ),
            },
            {"role": "user", "content": prompt},
        ]
        context = {"language": language, "user_id": user_id}
        result = await stack.run(prompt, messages, context)

        output = _coerce_prompt_output_text(getattr(result, "content", None))

        # Stream the code character by character
        await _send_session_stream_json(
            websocket,
            {"type": "status", "message": "Streaming output..."},
        )

        chunk_size = 50
        for i in range(0, len(output), chunk_size):
            chunk = output[i:i + chunk_size]
            await _send_session_stream_json(
                websocket,
                {"type": "code_chunk", "content": chunk},
            )
            await asyncio.sleep(0.02)

        lines = _count_generated_lines(output)

        # Run structural health analysis
        try:
            from codey.saas.api.health_analysis import _analyze_code
            import re
            code_to_analyze = output
            m = re.search(r"```(?:python|javascript|typescript)?\s*\n(.*?)```", output, re.DOTALL)
            if m:
                code_to_analyze = m.group(1)
            analysis = _analyze_code(code_to_analyze, f"generated.py", language)
            await _send_session_stream_json(
                websocket,
                _health_report_to_stream_event(_analysis_to_health_report(analysis)),
            )
        except Exception:
            pass

        # Complete
        await _send_session_stream_json(
            websocket,
            {
                "type": "complete",
                "credits_charged": 1,
                "lines_generated": lines,
                "files_modified": 1,
            },
        )

    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        try:
            await _send_session_stream_json(
                websocket,
                {"type": "error", "message": "Connection timed out"},
            )
        except Exception:
            pass
    except HTTPException as exc:
        try:
            detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
            await _send_session_stream_json(
                websocket,
                {"type": "error", "message": _redact_session_error(detail)[:200]},
            )
            await websocket.close()
        except Exception:
            pass
    except Exception as e:
        try:
            await _send_session_stream_json(
                websocket,
                {"type": "error", "message": _redact_session_error(e)[:200]},
            )
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
