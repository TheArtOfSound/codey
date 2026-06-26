from __future__ import annotations

import io
import ipaddress
import json
import math
import re
import uuid
import zipfile
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.public_urls import get_public_api_base_url
from codey.saas.auth.dependencies import get_current_user
from codey.saas.archive_utils import (
    dedupe_archive_path,
    safe_archive_path,
    safe_artifact_name,
)
from codey.saas.database import get_db
from codey.saas.models import Export, Project, ProjectVersion, User
from codey.saas.vault.service import VaultService

router = APIRouter(prefix="/vault", tags=["vault"])
_HTTP_URL_RE = re.compile(r"\bhttps?://[^\s'\"<>)]*", re.IGNORECASE)
_VAULT_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|password|secret|token|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_VAULT_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


class FileNodeResponse(BaseModel):
    name: str
    type: str
    children: list["FileNodeResponse"] | None = None
    lines: int | None = None


class VaultVersionResponse(BaseModel):
    id: str
    version: int
    created_at: str
    health_score: float | None
    prompt_summary: str
    lines_changed: int


class VaultProjectResponse(BaseModel):
    id: str
    name: str
    language: str
    last_active: str
    line_count: int
    health_score: float | None
    session_count: int
    versions: list[VaultVersionResponse]
    file_tree: list[FileNodeResponse]


class RestoreVersionRequest(BaseModel):
    version_id: str


class RestoreVersionResponse(BaseModel):
    version_id: str
    version_number: int
    message: str


class ExportRequest(BaseModel):
    project_id: str
    export_type: str
    github_repo: str | None = None
    github_branch: str | None = None
    webhook_url: str | None = None


class ExportResponse(BaseModel):
    id: str
    status: str


class ExportHistoryResponse(BaseModel):
    id: str
    project_name: str
    export_type: str
    status: str
    created_at: str
    download_url: str | None
    destination: str


FileNodeResponse.model_rebuild()


def _serialize_vault_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _coerce_snapshot_mapping(value: object) -> dict[str, Any] | None:
    return VaultService._coerce_vault_mapping(value)


def _coerce_non_empty_vault_text(value: object) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return None


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _parse_vault_obfuscated_ipv4(hostname: str) -> ipaddress.IPv4Address | None:
    parts = hostname.split(".")
    if not 1 <= len(parts) <= 4:
        return None

    values: list[int] = []
    for part in parts:
        if not part:
            return None
        base = 10
        digits = part
        if part.lower().startswith("0x"):
            base = 16
            digits = part[2:]
        elif len(part) > 1 and part.startswith("0"):
            base = 8
        if not digits:
            return None
        try:
            values.append(int(part, base))
        except ValueError:
            return None

    if len(values) == 1:
        if values[0] > 0xFFFFFFFF:
            return None
        address = values[0]
    elif len(values) == 2:
        if values[0] > 0xFF or values[1] > 0xFFFFFF:
            return None
        address = (values[0] << 24) | values[1]
    elif len(values) == 3:
        if values[0] > 0xFF or values[1] > 0xFF or values[2] > 0xFFFF:
            return None
        address = (values[0] << 24) | (values[1] << 16) | values[2]
    else:
        if any(value > 0xFF for value in values):
            return None
        address = (
            (values[0] << 24)
            | (values[1] << 16)
            | (values[2] << 8)
            | values[3]
        )
    return ipaddress.IPv4Address(address)


def _redact_vault_error(value: object) -> str:
    text = str(value)

    def _redact_url(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        try:
            parsed = urlparse(raw_url)
            port = parsed.port
        except ValueError:
            return raw_url
        if not parsed.scheme or not parsed.netloc:
            return raw_url

        hostname = parsed.hostname or ""
        netloc = hostname
        if port is not None:
            netloc = f"{netloc}:{port}"
        query = "redacted=***" if parsed.query else ""
        return urlunparse(
            (parsed.scheme, netloc, parsed.path, parsed.params, query, "")
        )

    text = _HTTP_URL_RE.sub(_redact_url, text)
    text = _VAULT_NAMED_SECRET_RE.sub(r"\1***", text)
    return _VAULT_EMAIL_ADDRESS_RE.sub(r"***@\1", text)


def _vault_export_download_filename(destination: object, export_id: object) -> str:
    fallback = f"codey-export-{export_id}"
    return safe_artifact_name(
        _coerce_non_empty_vault_text(destination),
        default=fallback,
        suffix=".zip",
    )


def _vault_export_download_url(export_id: object, request: Request | None = None) -> str:
    api_base_url = get_public_api_base_url(request)
    if api_base_url:
        return f"{api_base_url}/vault/exports/{export_id}/download"
    return f"/vault/exports/{export_id}/download"


def _stringify_vault_content(value: Any, *, indent: int | None = None) -> str:
    if isinstance(value, str):
        return value
    try:
        value = VaultService._json_safe_vault_value(value, _coerce_unknown=False)
        if indent is None:
            return json.dumps(value, allow_nan=False)
        return json.dumps(value, indent=indent, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)


def _count_vault_content_lines(content: str) -> int:
    return sum(1 for line in content.splitlines() if line.strip())


def _build_vault_webhook_payload(project: Any, versions: list[Any]) -> dict[str, Any]:
    payload = {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "language": project.language,
            "framework": project.framework,
        },
        "versions": [
            {
                "version_number": version.version_number,
                "commit_message": version.commit_message,
                "created_at": _serialize_vault_timestamp(version.created_at) or "",
                "file_snapshot": _coerce_snapshot_mapping(version.file_snapshot),
            }
            for version in versions
        ],
    }
    return VaultService._json_safe_vault_value(payload)


def _coerce_vault_int(value: object, fallback: int = 0) -> int:
    normalized: float
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        normalized = value
    elif isinstance(value, str):
        value = value.strip()
        if not value:
            return fallback
        try:
            normalized = float(value)
        except ValueError:
            return fallback
    else:
        return fallback
    return int(normalized) if math.isfinite(normalized) else fallback


def _coerce_vault_float(value: object) -> float | None:
    normalized: float
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        normalized = float(value)
    elif isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            normalized = float(value)
        except ValueError:
            return None
    else:
        return None
    return normalized if math.isfinite(normalized) else None


def _coerce_vault_collection_size(value: object) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 0


def _normalize_vault_webhook_url(value: object) -> str | None:
    url = _coerce_non_empty_vault_text(value)
    if not url or _has_ascii_control(url):
        return None

    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and port <= 0:
        return None

    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname or hostname == "localhost" or hostname.endswith(".localhost"):
        return None
    try:
        host_ip = ipaddress.ip_address(hostname)
    except ValueError:
        host_ip = _parse_vault_obfuscated_ipv4(hostname)
    if host_ip is not None:
        if (
            host_ip.is_loopback
            or host_ip.is_private
            or host_ip.is_link_local
            or host_ip.is_multicast
            or host_ip.is_reserved
            or host_ip.is_unspecified
        ):
            return None

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            "",
        )
    )


def _coerce_vault_row_list(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _vault_project_name_map(projects: object) -> dict[object, str]:
    project_names: dict[object, str] = {}
    for project in _coerce_vault_row_list(projects):
        project_id = getattr(project, "id", None)
        if project_id is None:
            continue
        project_names[project_id] = (
            _coerce_non_empty_vault_text(getattr(project, "name", None))
            or "Project"
        )
    return project_names


def _export_to_response(export_record: Export) -> ExportResponse:
    return ExportResponse(
        id=str(export_record.id),
        status=_coerce_non_empty_vault_text(export_record.status) or "unknown",
    )


def _restore_to_response(restored: ProjectVersion, requested_version_number: object) -> RestoreVersionResponse:
    version_number = _coerce_vault_int(restored.version_number, 0)
    requested = _coerce_vault_int(requested_version_number, 0)
    return RestoreVersionResponse(
        version_id=str(restored.id),
        version_number=version_number,
        message=f"Project restored to version {requested}",
    )


def _coerce_project_versions(value: object) -> list[ProjectVersion]:
    if not isinstance(value, (list, tuple)):
        return []

    required_attrs = (
        "id",
        "version_number",
        "created_at",
        "es_score",
        "commit_message",
        "diff",
        "file_snapshot",
        "files_changed",
    )
    return [
        version
        for version in value
        if all(hasattr(version, attr) for attr in required_attrs)
    ]


def _snapshot_to_tree(snapshot: dict[str, Any] | None) -> list[FileNodeResponse]:
    class _SnapshotFileContent:
        def __init__(self, value: Any) -> None:
            self.value = value

    root: dict[str, Any] = {}
    for path, content in (_coerce_snapshot_mapping(snapshot) or {}).items():
        parts = [part for part in str(path).split("/") if part]
        if not parts:
            continue
        cursor = root
        for part in parts[:-1]:
            existing = cursor.get(part)
            if not isinstance(existing, dict):
                existing = {}
                cursor[part] = existing
            cursor = existing
        leaf = parts[-1]
        if isinstance(cursor.get(leaf), dict):
            continue
        cursor[leaf] = _SnapshotFileContent(content)

    def build(name: str, value: Any) -> FileNodeResponse:
        if isinstance(value, dict):
            children = [build(child_name, child_value) for child_name, child_value in sorted(value.items())]
            return FileNodeResponse(name=name, type="directory", children=children)
        if isinstance(value, _SnapshotFileContent):
            value = value.value
        text = _stringify_vault_content(value)
        return FileNodeResponse(
            name=name,
            type="file",
            lines=_count_vault_content_lines(text),
        )

    return [build(name, value) for name, value in sorted(root.items())]


def _summarize_version(version: ProjectVersion) -> VaultVersionResponse:
    prompt_summary = _coerce_non_empty_vault_text(version.commit_message) or "Version snapshot"
    lines_changed = 0
    diff_text = _coerce_non_empty_vault_text(version.diff)
    if diff_text:
        lines_changed = len(diff_text.splitlines())
    else:
        file_snapshot = _coerce_snapshot_mapping(version.file_snapshot)
        if file_snapshot:
            lines_changed = sum(
                _count_vault_content_lines(_stringify_vault_content(content))
                for content in file_snapshot.values()
            )
    if lines_changed == 0:
        lines_changed = _coerce_vault_collection_size(version.files_changed)

    return VaultVersionResponse(
        id=str(version.id),
        version=_coerce_vault_int(version.version_number, 0),
        created_at=_serialize_vault_timestamp(version.created_at) or "",
        health_score=_coerce_vault_float(version.es_score),
        prompt_summary=prompt_summary,
        lines_changed=lines_changed,
    )


@router.get("/projects", response_model=list[VaultProjectResponse])
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[VaultProjectResponse]:
    stmt = (
        select(Project)
        .where(Project.user_id == current_user.id, Project.is_archived.is_(False))
        .order_by(Project.last_activity.desc().nullslast(), Project.created_at.desc())
    )
    result = await db.execute(stmt)
    projects = _coerce_vault_row_list(result.scalars().unique().all())

    responses: list[VaultProjectResponse] = []
    for project in projects:
        versions = _coerce_project_versions(getattr(project, "versions", None))
        latest_snapshot = (
            _coerce_snapshot_mapping(versions[0].file_snapshot)
            if versions
            else {}
        ) or {}
        line_count = 0
        for content in latest_snapshot.values():
            payload = _stringify_vault_content(content)
            line_count += _count_vault_content_lines(payload)

        responses.append(
            VaultProjectResponse(
                id=str(project.id),
                name=_coerce_non_empty_vault_text(project.name) or "Project",
                language=_coerce_non_empty_vault_text(project.language) or "Unknown",
                last_active=_serialize_vault_timestamp(project.last_activity or project.created_at)
                or "",
                line_count=line_count,
                health_score=_coerce_vault_float(project.latest_es_score),
                session_count=_coerce_vault_int(project.total_sessions, 0),
                versions=[_summarize_version(version) for version in versions],
                file_tree=_snapshot_to_tree(
                    _coerce_snapshot_mapping(project.file_tree) or latest_snapshot
                ),
            )
        )

    return responses


@router.post("/projects/{project_id}/restore", response_model=RestoreVersionResponse)
async def restore_project_version(
    project_id: str,
    body: RestoreVersionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RestoreVersionResponse:
    try:
        project_uuid = uuid.UUID(project_id)
        version_uuid = uuid.UUID(body.version_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project or version id",
        )

    version_result = await db.execute(
        select(ProjectVersion).where(
            ProjectVersion.id == version_uuid,
            ProjectVersion.project_id == project_uuid,
        )
    )
    version = version_result.scalar_one_or_none()
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )

    project = await db.get(Project, project_uuid)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    service = VaultService(db)
    try:
        restored = await service.restore_version(project_uuid, version.version_number)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        ) from exc
    project.file_tree = _coerce_snapshot_mapping(restored.file_snapshot) or project.file_tree
    project.last_activity = datetime.utcnow()
    await db.flush()

    return _restore_to_response(restored, version.version_number)


@router.get("/exports", response_model=list[ExportHistoryResponse])
async def list_exports(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ExportHistoryResponse]:
    service = VaultService(db)
    exports = await service.get_exports(current_user.id)

    project_names: dict[object, str] = {}
    if exports:
        project_ids = {export.project_id for export in exports}
        result = await db.execute(select(Project).where(Project.id.in_(project_ids)))
        project_names = _vault_project_name_map(result.scalars().all())

    history: list[ExportHistoryResponse] = []
    for export in exports:
        raw_export_type = _coerce_non_empty_vault_text(export.export_type)
        export_type = "download" if raw_export_type == "zip" else raw_export_type or "unknown"
        status_text = _coerce_non_empty_vault_text(export.status) or "unknown"
        metadata = _coerce_snapshot_mapping(export.metadata_) or {}
        project_name = _coerce_non_empty_vault_text(project_names.get(export.project_id)) or "Project"
        metadata_project_name = _coerce_non_empty_vault_text(metadata.get("project_name"))
        file_url = _coerce_non_empty_vault_text(export.file_url)
        history.append(
            ExportHistoryResponse(
                id=str(export.id),
                project_name=project_name,
                export_type=export_type,
                status="pending" if status_text == "processing" else status_text,
                created_at=_serialize_vault_timestamp(export.created_at) or "",
                download_url=(
                    _vault_export_download_url(export.id, request)
                    if status_text == "completed" and raw_export_type == "zip"
                    else file_url
                ),
                destination=_coerce_non_empty_vault_text(export.destination) or metadata_project_name or "",
            )
        )
    return history


@router.post("/exports", response_model=ExportResponse, status_code=status.HTTP_201_CREATED)
async def create_export(
    body: ExportRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExportResponse:
    try:
        project_uuid = uuid.UUID(body.project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project id",
        )

    project = await db.get(Project, project_uuid)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
    )

    service = VaultService(db)
    export_type = _coerce_non_empty_vault_text(body.export_type) or ""

    if export_type == "download":
        versions = await service.get_project_versions(project_uuid)
        if not versions:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No project versions available for export",
            )
        latest_snapshot = _coerce_snapshot_mapping(versions[0].file_snapshot) or {}
        if not latest_snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No project files available for export",
            )
        export_record = await service.export_project(
            current_user.id,
            project_uuid,
            "zip",
            destination=f"{project.name}.zip",
        )
        return _export_to_response(export_record)

    if export_type == "github":
        github_repo = _coerce_non_empty_vault_text(body.github_repo)
        github_branch = _coerce_non_empty_vault_text(body.github_branch)
        destination = github_repo or project.name
        if github_branch:
            destination = f"{destination} ({github_branch})"
        export_record = await service.export_project(
            current_user.id,
            project_uuid,
            "github",
            destination=destination,
        )
        return _export_to_response(export_record)

    if export_type == "webhook":
        raw_webhook_url = _coerce_non_empty_vault_text(body.webhook_url)
        if not raw_webhook_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook URL is required for webhook exports",
            )
        webhook_url = _normalize_vault_webhook_url(raw_webhook_url)
        if not webhook_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook URL must be a valid HTTP(S) URL",
            )

        versions = await service.get_project_versions(project_uuid)
        if not versions:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No project versions available for export",
            )
        payload = _build_vault_webhook_payload(project, versions)

        export_record = Export(
            user_id=current_user.id,
            project_id=project_uuid,
            export_type="webhook",
            destination=webhook_url,
            status="processing",
        )
        db.add(export_record)
        await db.flush()

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(webhook_url, json=payload)
                response.raise_for_status()
            export_record.status = "completed"
            export_record.completed_at = datetime.utcnow()
            export_record.metadata_ = {
                "delivered_versions": len(versions),
                "project_name": project.name,
            }
        except Exception as exc:
            export_record.status = "failed"
            export_record.error_message = _redact_vault_error(exc)
        await db.flush()
        return _export_to_response(export_record)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported export type",
    )


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    try:
        export_uuid = uuid.UUID(export_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid export id",
        )

    export_record = await db.get(Export, export_uuid)
    if export_record is None or export_record.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Export not found",
        )

    if export_record.export_type != "zip" or export_record.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This export is not available for download",
        )

    service = VaultService(db)
    versions = await service.get_project_versions(export_record.project_id)
    if not versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No project versions available for export",
        )

    latest = versions[0]
    snapshot = _coerce_snapshot_mapping(latest.file_snapshot) or {}
    if not snapshot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No project files available for export",
        )
    buffer = io.BytesIO()
    seen_archive_paths: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path, content in snapshot.items():
            archive.writestr(
                dedupe_archive_path(
                    safe_archive_path(file_path),
                    seen_archive_paths,
                ),
                _stringify_vault_content(content, indent=2),
            )
    buffer.seek(0)

    filename = _vault_export_download_filename(export_record.destination, export_record.id)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
