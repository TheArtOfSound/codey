from __future__ import annotations

import json
import math
import re
import shutil
import uuid
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.auth.websockets import authenticate_websocket
from codey.saas.archive_utils import (
    dedupe_archive_path,
    safe_archive_path,
    safe_artifact_name,
)
from codey.saas.build_mode.engine import BuildEngine
from codey.saas.build_mode.path_utils import normalize_plan_file_path
from codey.saas.credits.service import CreditService, InsufficientCreditsError, CREDIT_COSTS
from codey.saas.database import get_db
from codey.saas.intelligence.providers import call_model, resolve_model, set_byok_override
from codey.saas.models import BuildCheckpoint, BuildFile, BuildProject, User

async def _apply_byok(current_user: User = Depends(get_current_user)) -> None:
    """Apply the caller's BYOK override (if any) for this request."""
    try:
        set_byok_override(current_user.byok_provider, current_user.byok_api_key, current_user.byok_model)
    except Exception:
        pass


router = APIRouter(prefix="/build", tags=["build"], dependencies=[Depends(_apply_byok)])

_URL_CREDENTIAL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|token|secret|password)=)[^&\s]+"
)
_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|token|secret|password|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BuildStartRequest(BaseModel):
    description: str


class ClarificationQuestion(BaseModel):
    id: str
    question: str
    default: str | None = None
    options: list[str] | None = None


class TemplateMatch(BaseModel):
    template_id: str
    name: str
    confidence: float
    estimated_credits: int


class BuildStartResponse(BaseModel):
    questions: list[ClarificationQuestion]
    defaults: dict[str, str]
    template_match: TemplateMatch | None = None


class BuildPlanRequest(BaseModel):
    description: str
    answers: dict[str, str] | None = None


class PlanPhase(BaseModel):
    phase: int
    name: str
    files: list[str]
    description: str


class FileTreeNode(BaseModel):
    name: str
    type: str  # "file" | "directory"
    children: list[FileTreeNode] | None = None
    language: str | None = None


FileTreeNode.model_rebuild()


class BuildPlanResponse(BaseModel):
    project_id: str
    name: str
    stack: dict[str, Any]
    file_tree: list[FileTreeNode]
    phases: list[PlanPhase]
    total_files: int
    estimated_credits: int
    estimated_lines: int


class BuildApproveResponse(BaseModel):
    project_id: str
    session_id: str
    status: str


class BuildProjectResponse(BaseModel):
    id: str
    name: str | None
    description: str | None
    status: str
    current_phase: int
    total_phases: int | None
    files_planned: int | None
    files_completed: int
    lines_generated: int
    credits_charged: int
    nfet_es_score_final: float | None
    nfet_phase_final: str | None
    project_plan: dict[str, Any] | None
    file_tree: dict[str, Any] | None
    stack: dict[str, Any] | None
    download_url: str | None
    github_repo_url: str | None
    started_at: str
    completed_at: str | None


class BuildFileResponse(BaseModel):
    id: str
    file_path: str
    line_count: int | None
    phase: int | None
    status: str
    stress_score: float | None
    validation_passed: bool | None
    generated_at: str | None


class BuildFileDetailResponse(BuildFileResponse):
    content: str | None


class CheckpointRequest(BaseModel):
    action: str  # "continue" | "review" | "modify"
    notes: str | None = None


class CheckpointResponse(BaseModel):
    id: str
    project_id: str
    phase: int | None
    phase_name: str | None
    files_in_phase: int | None
    tests_passed: int | None
    tests_failed: int | None
    nfet_es_score: float | None
    nfet_kappa: float | None
    nfet_sigma: float | None
    user_action: str | None
    checkpoint_at: str


class DownloadResponse(BaseModel):
    download_url: str
    filename: str
    size_bytes: int


class TemplateInfo(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    estimated_credits: int
    languages: list[str]
    files_count: int


def _coerce_answer_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    answers: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(raw, str):
            answers[key] = raw
        elif raw is not None:
            answers[key] = str(raw)
    return answers


def _coerce_plan_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_plan_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ""


def _extract_plan_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _coerce_plan_mapping(value)

    text = _coerce_plan_text(value).strip()
    if not text:
        return {}

    try:
        return _coerce_plan_mapping(json.loads(text))
    except json.JSONDecodeError:
        pass

    if "```" in text:
        import re

        match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if match:
            try:
                return _coerce_plan_mapping(json.loads(match.group(1)))
            except json.JSONDecodeError:
                pass

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            return _coerce_plan_mapping(json.loads(text[first_brace : last_brace + 1]))
        except json.JSONDecodeError:
            pass

    return {}


def _coerce_phase_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def _coerce_phase_name(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate:
            return candidate
    return fallback


def _coerce_phase_files(value: Any) -> list[str]:
    if isinstance(value, str):
        candidate = normalize_plan_file_path(value)
        return [candidate] if candidate else []
    if not isinstance(value, list):
        return []
    files: list[str] = []
    seen: set[str] = set()
    for raw in value:
        candidate = normalize_plan_file_path(raw)
        if candidate and candidate not in seen:
            seen.add(candidate)
            files.append(candidate)
    return files


def _coerce_estimated_credits(value: Any, fallback: int) -> int:
    if isinstance(value, bool) or value is None:
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(1, min(parsed, 10_000))


def _default_build_plan_phases(total_phases: int) -> list[dict[str, Any]]:
    return [
        {"name": "Project Setup & Configuration", "files": ["requirements.txt", "pyproject.toml", ".env.example", "Dockerfile"]},
        {"name": "Core Data Models & Database", "files": ["app/models.py", "app/database.py", "migrations/init.sql"]},
        {"name": "Business Logic & API", "files": ["app/main.py", "app/routes.py", "app/services.py", "app/auth.py"]},
        {"name": "Tests & Documentation", "files": ["tests/test_routes.py", "tests/test_services.py", "README.md"]},
    ][:total_phases]


# ---------------------------------------------------------------------------
# Templates registry
# ---------------------------------------------------------------------------

TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "saas-starter",
        "name": "SaaS Starter",
        "description": "Full-stack SaaS boilerplate with auth, billing, and dashboard",
        "icon": "rocket",
        "estimated_credits": 25,
        "languages": ["TypeScript", "Python"],
        "files_count": 32,
    },
    {
        "id": "rest-api",
        "name": "REST API",
        "description": "Production-ready REST API with auth, validation, and docs",
        "icon": "server",
        "estimated_credits": 15,
        "languages": ["Python", "SQL"],
        "files_count": 18,
    },
    {
        "id": "react-app",
        "name": "React App",
        "description": "Modern React app with routing, state management, and testing",
        "icon": "layout",
        "estimated_credits": 18,
        "languages": ["TypeScript", "CSS"],
        "files_count": 24,
    },
    {
        "id": "cli-tool",
        "name": "CLI Tool",
        "description": "Command-line application with argument parsing and config",
        "icon": "terminal",
        "estimated_credits": 8,
        "languages": ["Python"],
        "files_count": 10,
    },
    {
        "id": "discord-bot",
        "name": "Discord Bot",
        "description": "Discord bot with slash commands, events, and database",
        "icon": "message-circle",
        "estimated_credits": 12,
        "languages": ["Python", "SQL"],
        "files_count": 14,
    },
    {
        "id": "data-pipeline",
        "name": "Data Pipeline",
        "description": "ETL pipeline with scheduling, monitoring, and error handling",
        "icon": "database",
        "estimated_credits": 14,
        "languages": ["Python", "SQL"],
        "files_count": 16,
    },
    {
        "id": "mobile-api",
        "name": "Mobile API",
        "description": "Backend API optimized for mobile clients with push notifications",
        "icon": "smartphone",
        "estimated_credits": 16,
        "languages": ["Python", "TypeScript"],
        "files_count": 20,
    },
    {
        "id": "ecommerce",
        "name": "E-commerce",
        "description": "Online store with products, cart, checkout, and payments",
        "icon": "shopping-cart",
        "estimated_credits": 28,
        "languages": ["TypeScript", "Python", "SQL"],
        "files_count": 36,
    },
]


# ---------------------------------------------------------------------------
# Helper: resolve project with ownership check
# ---------------------------------------------------------------------------


async def _get_project(
    project_id: str,
    user: User,
    db: AsyncSession,
) -> BuildProject:
    return await _get_project_for_user_id(project_id, user.id, db)


async def _get_project_for_user_id(
    project_id: str,
    user_id: str | uuid.UUID,
    db: AsyncSession,
) -> BuildProject:
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project ID format",
        )

    try:
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format",
        )

    stmt = select(BuildProject).where(
        BuildProject.id == pid,
        BuildProject.user_id == uid,
    )
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Build project not found",
        )

    return project


def _download_endpoint(project_id: str) -> str:
    return f"/build/{project_id}/download/zip"


async def _generate_project_zip(
    project: BuildProject,
    db: AsyncSession,
) -> tuple[Path, int]:
    stmt = select(BuildFile).where(
        BuildFile.project_id == project.id,
        BuildFile.status == "completed",
    )
    result = await db.execute(stmt)
    files = _coerce_build_row_list(result.scalars().all())
    if not files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No completed build files available for download",
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="codey_build_"))
    zip_path = temp_dir / safe_artifact_name(
        project.name,
        default="project",
        suffix=".zip",
    )

    previous_download_url = getattr(project, "download_url", None)
    cleanup_temp_dir = True
    try:
        written_files = 0
        seen_archive_paths: set[str] = set()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for build_file in files:
                content = getattr(build_file, "content", None)
                if content is None:
                    continue
                if not isinstance(content, (str, bytes)):
                    content = str(content)
                archive_path = dedupe_archive_path(
                    safe_archive_path(build_file.file_path),
                    seen_archive_paths,
                )
                zf.writestr(
                    archive_path,
                    content,
                )
                written_files += 1

        if written_files == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No completed build files available for download",
            )

        size = zip_path.stat().st_size
        project.download_url = str(zip_path)
        await db.flush()
        cleanup_temp_dir = False
        return zip_path, size
    finally:
        if cleanup_temp_dir:
            if getattr(project, "download_url", None) == str(zip_path):
                project.download_url = previous_download_url
            shutil.rmtree(temp_dir, ignore_errors=True)


def _cleanup_generated_zip(zip_path: Path) -> None:
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass

    # Request-scoped downloads are generated in their own temp directories
    # (``codey_build_*``). Build-engine artifacts may live in a shared
    # ``codey_builds`` directory and must not trigger a full parent wipe.
    try:
        parent = zip_path.parent.resolve(strict=False)
        temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    except OSError:
        return
    if (
        zip_path.parent.name.startswith("codey_build_")
        and temp_root in {parent, *parent.parents}
    ):
        shutil.rmtree(zip_path.parent, ignore_errors=True)


def _serialize_build_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _coerce_build_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _coerce_build_content(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return None


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _coerce_build_public_url(value: Any) -> str | None:
    url = _coerce_build_text(value)
    if url is None or _has_ascii_control(url):
        return None
    if "?" in url or "#" in url:
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.netloc or not parsed.hostname:
        return None
    if port is not None and not (1 <= port <= 65535):
        return None
    if parsed.username or parsed.password:
        return None
    return url


def _redact_build_route_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _NAMED_SECRET_RE.sub(r"\1***", text)
    return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)


def _coerce_generated_file_content(value: Any) -> str:
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
                return _coerce_generated_file_content(candidate)
            except TypeError:
                continue
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            try:
                candidate = _coerce_generated_file_content(item)
            except TypeError:
                continue
            if candidate:
                parts.append(candidate)
        if parts:
            return "\n".join(parts)
    raise TypeError("Model returned non-text generated file content")


def _count_generated_file_lines(content: str) -> int:
    return sum(1 for line in content.splitlines() if line.strip())


def _coerce_build_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        candidate = value.strip()
        return [candidate] if candidate else []
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for raw in value:
        candidate = _coerce_build_text(raw)
        if candidate:
            values.append(candidate)
    return values


def _coerce_build_int(value: Any, fallback: int = 0) -> int:
    normalized: float
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        normalized = value
    elif isinstance(value, str):
        try:
            normalized = float(value.strip())
        except ValueError:
            return fallback
    else:
        return fallback
    return int(normalized) if math.isfinite(normalized) else fallback


def _coerce_optional_build_int(value: Any) -> int | None:
    if value is None:
        return None
    return _coerce_build_int(value, 0)


def _coerce_optional_build_float(value: Any) -> float | None:
    normalized: float
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        normalized = float(value)
    elif isinstance(value, str):
        try:
            normalized = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return normalized if math.isfinite(normalized) else None


def _coerce_optional_build_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _coerce_optional_build_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    return None


def _coerce_build_row_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _coerce_existing_zip_path(value: Any) -> Path | None:
    path_text = _coerce_build_text(value)
    if not path_text or _has_ascii_control(path_text):
        return None
    path = Path(path_text)
    if not path.is_absolute():
        return None
    if path.suffix.lower() != ".zip":
        return None
    try:
        resolved_path = path.resolve(strict=False)
        temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    except OSError:
        return None
    if temp_root not in {resolved_path, *resolved_path.parents}:
        return None
    try:
        relative_parts = resolved_path.relative_to(temp_root).parts
    except ValueError:
        return None
    if not relative_parts or (
        relative_parts[0] != "codey_builds"
        and not relative_parts[0].startswith("codey_build_")
    ):
        return None
    return resolved_path


def _existing_zip_size(path: Path) -> int | None:
    try:
        if not path.is_file():
            return None
        return path.stat().st_size
    except OSError:
        return None


def _template_to_response(template: Any) -> TemplateInfo:
    payload = template if isinstance(template, dict) else {}
    template_id = _coerce_build_text(payload.get("id")) or ""
    return TemplateInfo(
        id=template_id,
        name=_coerce_build_text(payload.get("name")) or template_id or "Template",
        description=_coerce_build_text(payload.get("description")) or "",
        icon=_coerce_build_text(payload.get("icon")) or "",
        estimated_credits=_coerce_build_int(payload.get("estimated_credits"), 0),
        languages=_coerce_build_string_list(payload.get("languages")),
        files_count=_coerce_build_int(payload.get("files_count"), 0),
    )


def _project_to_response(project: BuildProject) -> BuildProjectResponse:
    status = _coerce_build_text(project.status) or "unknown"
    files_completed = _coerce_build_int(project.files_completed, 0)
    cached_download_url = _coerce_build_text(project.download_url)
    has_download = status == "completed" and (
        bool(cached_download_url) or files_completed > 0
    )
    return BuildProjectResponse(
        id=str(project.id),
        name=_coerce_build_text(project.name),
        description=_coerce_build_text(project.description),
        status=status,
        current_phase=_coerce_build_int(project.current_phase, 0),
        total_phases=_coerce_optional_build_int(project.total_phases),
        files_planned=_coerce_optional_build_int(project.files_planned),
        files_completed=files_completed,
        lines_generated=_coerce_build_int(project.lines_generated, 0),
        credits_charged=_coerce_build_int(project.credits_charged, 0),
        nfet_es_score_final=_coerce_optional_build_float(project.nfet_es_score_final),
        nfet_phase_final=_coerce_build_text(project.nfet_phase_final),
        project_plan=_coerce_optional_build_dict(project.project_plan),
        file_tree=_coerce_optional_build_dict(project.file_tree),
        stack=_coerce_optional_build_dict(project.stack),
        download_url=(
            _download_endpoint(str(project.id))
            if has_download
            else None
        ),
        github_repo_url=_coerce_build_public_url(project.github_repo_url),
        started_at=_serialize_build_timestamp(project.started_at) or "",
        completed_at=_serialize_build_timestamp(project.completed_at),
    )


def _file_to_response(f: BuildFile) -> BuildFileResponse:
    return BuildFileResponse(
        id=str(f.id),
        file_path=_coerce_build_text(f.file_path) or "",
        line_count=_coerce_optional_build_int(f.line_count),
        phase=_coerce_optional_build_int(f.phase),
        status=_coerce_build_text(f.status) or "unknown",
        stress_score=_coerce_optional_build_float(f.stress_score),
        validation_passed=_coerce_optional_build_bool(f.validation_passed),
        generated_at=_serialize_build_timestamp(f.generated_at),
    )


def _file_to_detail(f: BuildFile) -> BuildFileDetailResponse:
    return BuildFileDetailResponse(
        id=str(f.id),
        file_path=_coerce_build_text(f.file_path) or "",
        content=_coerce_build_content(f.content),
        line_count=_coerce_optional_build_int(f.line_count),
        phase=_coerce_optional_build_int(f.phase),
        status=_coerce_build_text(f.status) or "unknown",
        stress_score=_coerce_optional_build_float(f.stress_score),
        validation_passed=_coerce_optional_build_bool(f.validation_passed),
        generated_at=_serialize_build_timestamp(f.generated_at),
    )


def _checkpoint_to_response(
    checkpoint: BuildCheckpoint,
    project_id: object,
) -> CheckpointResponse:
    return CheckpointResponse(
        id=str(checkpoint.id),
        project_id=str(project_id),
        phase=_coerce_optional_build_int(checkpoint.phase),
        phase_name=_coerce_build_text(checkpoint.phase_name),
        files_in_phase=_coerce_optional_build_int(checkpoint.files_in_phase),
        tests_passed=_coerce_optional_build_int(checkpoint.tests_passed),
        tests_failed=_coerce_optional_build_int(checkpoint.tests_failed),
        nfet_es_score=_coerce_optional_build_float(checkpoint.nfet_es_score),
        nfet_kappa=_coerce_optional_build_float(checkpoint.nfet_kappa),
        nfet_sigma=_coerce_optional_build_float(checkpoint.nfet_sigma),
        user_action=_coerce_build_text(checkpoint.user_action),
        checkpoint_at=_serialize_build_timestamp(checkpoint.checkpoint_at) or "",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/start", response_model=BuildStartResponse, status_code=status.HTTP_200_OK)
async def build_start(
    body: BuildStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BuildStartResponse:
    """Analyze a project description and return clarification questions,
    inferred defaults, and best template match."""

    description_lower = body.description.lower()

    # --- Template matching (keyword heuristic) ---
    template_match: TemplateMatch | None = None
    keyword_map: dict[str, list[str]] = {
        "saas-starter": ["saas", "subscription", "billing", "multi-tenant"],
        "rest-api": ["rest", "api", "endpoints", "crud"],
        "react-app": ["react", "frontend", "ui", "dashboard", "spa"],
        "cli-tool": ["cli", "command line", "terminal", "script"],
        "discord-bot": ["discord", "bot", "slash command"],
        "data-pipeline": ["etl", "pipeline", "data", "scraping", "ingestion"],
        "mobile-api": ["mobile", "ios", "android", "push notification"],
        "ecommerce": ["ecommerce", "e-commerce", "shop", "cart", "checkout", "store"],
    }

    best_template_id: str | None = None
    best_score = 0.0
    for tid, keywords in keyword_map.items():
        hits = sum(1 for kw in keywords if kw in description_lower)
        if hits > 0:
            score = hits / len(keywords)
            if score > best_score:
                best_score = score
                best_template_id = tid

    if best_template_id:
        tpl = next((t for t in TEMPLATES if t["id"] == best_template_id), None)
        if tpl:
            template_match = TemplateMatch(
                template_id=tpl["id"],
                name=tpl["name"],
                confidence=round(best_score, 2),
                estimated_credits=tpl["estimated_credits"],
            )

    # --- Clarification questions ---
    questions: list[ClarificationQuestion] = []
    defaults: dict[str, str] = {}

    # Language
    detected_lang = "Python"
    for lang_kw, lang_name in [
        ("typescript", "TypeScript"),
        ("javascript", "JavaScript"),
        ("python", "Python"),
        ("go ", "Go"),
        ("golang", "Go"),
        ("rust", "Rust"),
        ("java ", "Java"),
    ]:
        if lang_kw in description_lower:
            detected_lang = lang_name
            break
    defaults["language"] = detected_lang
    questions.append(ClarificationQuestion(
        id="language",
        question="What primary language should the project use?",
        default=detected_lang,
        options=["Python", "TypeScript", "JavaScript", "Go", "Rust", "Java"],
    ))

    # Framework
    defaults["framework"] = "auto"
    questions.append(ClarificationQuestion(
        id="framework",
        question="Any specific framework preference?",
        default="auto",
    ))

    # Database
    defaults["database"] = "PostgreSQL"
    questions.append(ClarificationQuestion(
        id="database",
        question="What database should be used?",
        default="PostgreSQL",
        options=["PostgreSQL", "SQLite", "MySQL", "MongoDB", "None"],
    ))

    # Testing
    defaults["testing"] = "yes"
    questions.append(ClarificationQuestion(
        id="testing",
        question="Include test suite?",
        default="yes",
        options=["yes", "no"],
    ))

    # Deployment
    defaults["deployment"] = "Docker"
    questions.append(ClarificationQuestion(
        id="deployment",
        question="Include deployment configuration?",
        default="Docker",
        options=["Docker", "Kubernetes", "Serverless", "None"],
    ))

    return BuildStartResponse(
        questions=questions,
        defaults=defaults,
        template_match=template_match,
    )


@router.post("/plan", response_model=BuildPlanResponse, status_code=status.HTTP_201_CREATED)
async def build_plan(
    body: BuildPlanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BuildPlanResponse:
    """Create a full build plan and persist the BuildProject row."""

    answers = _coerce_answer_mapping(body.answers)
    language = answers.get("language", "Python")
    framework = answers.get("framework", "auto")
    database = answers.get("database", "PostgreSQL")
    testing = answers.get("testing", "yes")
    deployment = answers.get("deployment", "Docker")

    # Estimate complexity
    desc_len = len(body.description)
    if desc_len < 200:
        total_files = 12
        estimated_lines = 1200
        total_phases = 3
    elif desc_len < 500:
        total_files = 22
        estimated_lines = 3500
        total_phases = 4
    else:
        total_files = 35
        estimated_lines = 6000
        total_phases = 5

    estimated_credits = max(
        CREDIT_COSTS["full_build"],
        int(estimated_lines / 250),
    )

    # Build stack info
    stack = {
        "language": language,
        "framework": framework,
        "database": database,
        "testing": testing == "yes",
        "deployment": deployment,
    }

    # Generate plan using LLM
    phases: list[dict[str, Any]] = []
    all_planned_files: list[str] = []
    llm_phases: list[dict[str, Any]] = []

    try:
        provider, model = resolve_model("architecture")
        plan_prompt = [
            {"role": "system", "content": (
                "You are a project architect. Given a project description, output a JSON build plan.\n"
                "Return ONLY valid JSON with this structure:\n"
                '{"phases": [{"name": "Phase Name", "files": ["path/to/file.py", ...]}], '
                '"estimated_credits": number}\n'
                "Use real file paths appropriate for the stack. 3-5 phases max."
            )},
            {"role": "user", "content": f"Project: {body.description}\nStack: {language}, {framework}, {database}"},
        ]
        raw = await call_model(provider, model, plan_prompt, max_tokens=2000, temperature=0.3)
        plan_data = _extract_plan_mapping(raw)
        llm_phases = _coerce_phase_entries(plan_data.get("phases"))
        estimated_credits = _coerce_estimated_credits(
            plan_data.get("estimated_credits"),
            estimated_credits,
        )
    except Exception:
        llm_phases = []
    if not llm_phases:
        llm_phases = _default_build_plan_phases(total_phases)
    seen_planned_files: set[str] = set()
    for i, phase_info in enumerate(llm_phases):
        phase_name = _coerce_phase_name(phase_info.get("name"), f"Phase {i+1}")
        phase_files = []
        for file_path in _coerce_phase_files(phase_info.get("files")):
            if file_path in seen_planned_files:
                continue
            seen_planned_files.add(file_path)
            phase_files.append(file_path)
        all_planned_files.extend(phase_files)
        phases.append({
            "phase": i + 1,
            "name": phase_name,
            "files": phase_files,
            "description": f"Phase {i + 1}: {phase_name}",
        })
    total_phases = len(phases) or 1
    total_files = len(all_planned_files)

    # Build file tree
    file_tree_data: list[dict[str, Any]] = [
        {
            "name": "src",
            "type": "directory",
            "children": [
                {"name": f.split("/")[-1], "type": "file", "language": language}
                for f in all_planned_files
            ],
        },
        {"name": "README.md", "type": "file", "language": "Markdown"},
        {"name": ".gitignore", "type": "file"},
    ]

    if testing == "yes":
        file_tree_data.append({
            "name": "tests",
            "type": "directory",
            "children": [
                {"name": "test_main.py" if language == "Python" else "main.test.ts", "type": "file"}
            ],
        })

    if deployment != "None":
        file_tree_data.append({"name": "Dockerfile", "type": "file"})
        file_tree_data.append({"name": "docker-compose.yml", "type": "file"})

    # Persist the project
    project = BuildProject(
        user_id=current_user.id,
        name=body.description[:100],
        description=body.description,
        status="planning",
        total_phases=total_phases,
        files_planned=total_files,
        project_plan={"phases": phases},
        file_tree={"tree": file_tree_data},
        stack=stack,
        started_at=datetime.utcnow(),
    )
    db.add(project)
    await db.flush()

    # Create BuildFile rows for all planned files
    for phase_data in phases:
        for fp in phase_data["files"]:
            bf = BuildFile(
                project_id=project.id,
                file_path=fp,
                phase=phase_data["phase"],
                status="pending",
            )
            db.add(bf)
    await db.flush()

    return BuildPlanResponse(
        project_id=str(project.id),
        name=project.name or body.description[:100],
        stack=stack,
        file_tree=[FileTreeNode(**node) for node in file_tree_data],
        phases=[PlanPhase(**p) for p in phases],
        total_files=total_files,
        estimated_credits=estimated_credits,
        estimated_lines=estimated_lines,
    )


@router.post(
    "/approve/{project_id}",
    response_model=BuildApproveResponse,
    status_code=status.HTTP_200_OK,
)
async def build_approve(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BuildApproveResponse:
    """Approve a plan and start the build. Reserves credits."""

    project = await _get_project(project_id, current_user, db)

    if project.status != "planning":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project is '{project.status}' — can only approve projects in 'planning' status",
        )

    # Estimate and reserve credits
    files_planned = (
        _coerce_build_int(getattr(project, "files_planned", None), 0) or 12
    )
    estimated = max(
        CREDIT_COSTS["full_build"],
        files_planned * 2,
    )

    credit_service = CreditService(db)
    try:
        await credit_service.reserve_credits(
            user_id=current_user.id,
            estimated_cost=estimated,
            description=f"Build project: {(project.name or 'Untitled')[:60]}",
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

    project.status = "building"
    project.credits_charged = estimated
    project.current_phase = 1
    await db.flush()

    # Generate ALL files across all phases
    try:
        all_files_result = await db.execute(
            select(BuildFile).where(
                BuildFile.project_id == project.id,
            ).order_by(BuildFile.phase)
        )
        all_files = _coerce_build_row_list(all_files_result.scalars().all())

        provider, model = resolve_model("code_generation")
        project_desc = project.description or ""
        generated_context: list[str] = []  # Track what's been built for context
        had_generation_failures = False

        for bf in all_files:
            try:
                file_path = normalize_plan_file_path(getattr(bf, "file_path", None))
                if not file_path:
                    raise ValueError("Invalid build file path")
                bf.file_path = file_path

                # Build context from previously generated files
                context_summary = ""
                if generated_context:
                    context_summary = "\n\nAlready generated files:\n" + "\n".join(
                        f"- {ctx}" for ctx in generated_context[-10:]  # Last 10 for context window
                    )

                gen_messages = [
                    {"role": "system", "content": (
                        "You are Codey, generating files for a complete project. "
                        "Return ONLY the file content. No markdown fences. No explanation. "
                        "The code must be production-quality, with proper imports, "
                        "error handling, type hints, and consistent with other project files."
                    )},
                    {"role": "user", "content": (
                        f"Project: {project_desc}\n"
                        f"File to generate: {file_path}\n"
                        f"{context_summary}\n"
                        f"Generate the complete, production-ready content for {file_path}."
                    )},
                ]
                content = _coerce_generated_file_content(
                    await call_model(provider, model, gen_messages, max_tokens=4096)
                )
                bf.content = content
                bf.line_count = _count_generated_file_lines(content)
                bf.status = "completed"
                bf.validation_passed = True
                project.files_completed = (project.files_completed or 0) + 1
                project.lines_generated = (project.lines_generated or 0) + bf.line_count

                # Add to context for next files
                generated_context.append(f"{file_path} ({bf.line_count} lines)")

            except Exception as gen_err:
                had_generation_failures = True
                bf.status = "failed"
                bf.validation_passed = False
                safe_error = _redact_build_route_error(gen_err)[:200]
                bf.content = f"# Generation failed: {safe_error}"

        project.status = "failed" if had_generation_failures else "completed"
        if project.status == "completed":
            project.completed_at = datetime.utcnow()
        await db.flush()
    except Exception:
        project.status = "failed"
        try:
            await credit_service.refund_credits(
                user_id=current_user.id,
                amount=estimated,
                description=f"Refund failed build project: {(project.name or 'Untitled')[:60]}",
                session_id=project.session_id,
            )
            project.credits_charged = 0
        except Exception:
            pass
        await db.flush()

    return BuildApproveResponse(
        project_id=str(project.id),
        session_id=str(project.session_id or project.id),
        status=project.status,
    )


@router.get("/templates", response_model=list[TemplateInfo])
async def list_templates(
    current_user: User = Depends(get_current_user),
) -> list[TemplateInfo]:
    """List available project templates."""
    return [_template_to_response(tpl) for tpl in TEMPLATES]


@router.get("/{project_id}", response_model=BuildProjectResponse)
async def get_build_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BuildProjectResponse:
    """Get build project status and details."""
    project = await _get_project(project_id, current_user, db)
    return _project_to_response(project)


@router.get("/{project_id}/files", response_model=list[BuildFileResponse])
async def get_build_files(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[BuildFileResponse]:
    """List all generated files with status."""
    project = await _get_project(project_id, current_user, db)

    stmt = (
        select(BuildFile)
        .where(BuildFile.project_id == project.id)
        .order_by(BuildFile.phase, BuildFile.file_path)
    )
    result = await db.execute(stmt)
    files = _coerce_build_row_list(result.scalars().all())

    return [_file_to_response(f) for f in files]


@router.get("/{project_id}/files/{file_id}", response_model=BuildFileDetailResponse)
async def get_build_file(
    project_id: str,
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BuildFileDetailResponse:
    """Get a specific file's content."""
    project = await _get_project(project_id, current_user, db)

    try:
        fid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file ID format",
        )

    stmt = select(BuildFile).where(
        BuildFile.id == fid,
        BuildFile.project_id == project.id,
    )
    result = await db.execute(stmt)
    build_file = result.scalar_one_or_none()

    if build_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Build file not found",
        )

    return _file_to_detail(build_file)


@router.post(
    "/{project_id}/checkpoint/{phase}",
    response_model=CheckpointResponse,
    status_code=status.HTTP_200_OK,
)
async def handle_checkpoint(
    project_id: str,
    phase: int,
    body: CheckpointRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CheckpointResponse:
    """Handle checkpoint action at end of a phase."""
    project = await _get_project(project_id, current_user, db)

    if body.action not in ("continue", "review", "modify"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Action must be 'continue', 'review', or 'modify'",
        )

    # Count files in this phase
    stmt = select(BuildFile).where(
        BuildFile.project_id == project.id,
        BuildFile.phase == phase,
    )
    result = await db.execute(stmt)
    phase_files = _coerce_build_row_list(result.scalars().all())

    tests_passed = sum(1 for f in phase_files if f.validation_passed is True)
    tests_failed = sum(1 for f in phase_files if f.validation_passed is False)

    # Create checkpoint record
    checkpoint = BuildCheckpoint(
        project_id=project.id,
        phase=phase,
        phase_name=f"Phase {phase}",
        files_in_phase=len(phase_files),
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        nfet_es_score=project.nfet_es_score_final,
        user_action=body.action,
        user_notes=body.notes,
        checkpoint_at=datetime.utcnow(),
    )
    db.add(checkpoint)

    # Update project state based on action
    if body.action == "continue":
        next_phase = phase + 1
        if project.total_phases and next_phase > project.total_phases:
            project.status = "completed"
            project.completed_at = datetime.utcnow()
        else:
            project.current_phase = next_phase
    elif body.action == "modify":
        project.status = "paused"

    await db.flush()

    return _checkpoint_to_response(checkpoint, project.id)


@router.get("/{project_id}/download", response_model=DownloadResponse)
async def get_download(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DownloadResponse:
    """Return download URL for a completed project zip."""
    project = await _get_project(project_id, current_user, db)

    if project.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project must be completed before downloading",
        )

    download_endpoint = _download_endpoint(project_id)

    # If a pre-generated artifact exists, return its public endpoint
    existing_zip = _coerce_existing_zip_path(project.download_url)
    if existing_zip is not None:
        existing_zip_size = _existing_zip_size(existing_zip)
        if existing_zip_size is not None:
            return DownloadResponse(
                download_url=download_endpoint,
                filename=existing_zip.name,
                size_bytes=existing_zip_size,
            )

    zip_path, size = await _generate_project_zip(project, db)

    return DownloadResponse(
        download_url=download_endpoint,
        filename=zip_path.name,
        size_bytes=size,
    )


@router.get("/{project_id}/download/zip")
async def download_project_zip(
    project_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Stream a generated project zip to the caller."""
    project = await _get_project(project_id, current_user, db)

    if project.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project must be completed before downloading",
        )

    zip_path = _coerce_existing_zip_path(project.download_url)
    generated_for_request = False
    if zip_path is None or _existing_zip_size(zip_path) is None:
        zip_path, _size = await _generate_project_zip(project, db)
        generated_for_request = True

    if generated_for_request:
        background_tasks.add_task(_cleanup_generated_zip, zip_path)
    return FileResponse(
        path=zip_path,
        filename=zip_path.name,
        media_type="application/zip",
    )


# ---------------------------------------------------------------------------
# WebSocket: real-time build progress stream
# ---------------------------------------------------------------------------


def _coerce_build_stream_message(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    return None


def _json_safe_build_stream_value(
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
                str(key): _json_safe_build_stream_value(item, _seen)
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
                _json_safe_build_stream_value(item, _seen)
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
            return [_json_safe_build_stream_value(item, _seen) for item in value]
        finally:
            _seen.remove(value_id)
    return str(value)


async def _send_build_stream_json(
    websocket: WebSocket,
    event: dict[str, Any],
) -> None:
    await websocket.send_json(_json_safe_build_stream_value(event))


@router.websocket("/{project_id}/stream")
async def build_stream(
    websocket: WebSocket,
    project_id: str,
    token: str | None = None,
) -> None:
    """Stream build progress in real-time via WebSocket.

    Messages sent to client follow the schema:
    {
        "type": "status" | "phase" | "file_start" | "file_chunk" | "file_complete"
               | "checkpoint" | "nfet" | "error" | "complete",
        "data": { ... },
        "timestamp": "ISO8601"
    }
    """
    payload = authenticate_websocket(websocket, token)
    if not payload:
        await websocket.close(code=1008, reason="Authentication required")
        return
    user_id = payload.get("sub")

    try:
        async for db in get_db():
            await _get_project_for_user_id(project_id, user_id, db)
            break
    except HTTPException as exc:
        reason = exc.detail if isinstance(exc.detail, str) else "Project access denied"
        await websocket.close(code=1008, reason=reason[:120])
        return
    except Exception:
        await websocket.close(code=1011, reason="Failed to validate project access")
        return

    await websocket.accept()

    # Send initial connection acknowledgment
    await _send_build_stream_json(
        websocket,
        {
            "type": "status",
            "data": {"message": "Connected to build stream", "project_id": project_id},
            "timestamp": datetime.utcnow().isoformat(),
        },
    )

    try:
        # Keep connection alive and relay build events
        # In production, this would subscribe to a message broker (Redis pub/sub, etc.)
        # and forward events from the build engine to the client.
        while True:
            # Listen for client messages (heartbeats, cancellation requests)
            data = await websocket.receive_text()
            try:
                msg = _coerce_build_stream_message(json.loads(data))
                if msg is None:
                    continue
                if msg.get("type") == "ping":
                    await _send_build_stream_json(
                        websocket,
                        {
                            "type": "pong",
                            "data": {},
                            "timestamp": datetime.utcnow().isoformat(),
                        },
                    )
                elif msg.get("type") == "cancel":
                    await _send_build_stream_json(
                        websocket,
                        {
                            "type": "status",
                            "data": {"message": "Build cancellation requested"},
                            "timestamp": datetime.utcnow().isoformat(),
                        },
                    )
            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close(code=1011, reason="Internal error")
        except Exception:
            pass
