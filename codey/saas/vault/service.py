"""Code Vault — project versioning, snapshot management, and export."""

from __future__ import annotations

import io
import json
import logging
import math
import re
import uuid
import zipfile
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.archive_utils import dedupe_archive_path, safe_archive_path
from codey.saas.models.export import Export
from codey.saas.models.project import Project
from codey.saas.models.project_version import ProjectVersion

logger = logging.getLogger(__name__)
_HTTP_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&#](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|token|secret)=)[^&#\s]+"
)
_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|token|secret|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


class VaultService:
    """Manages the Code Vault — versioned project storage with NFET state tracking."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    @staticmethod
    def _coerce_vault_mapping(value: object) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, (list, tuple)):
            try:
                return dict(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            try:
                parsed = json.loads(normalized)
            except ValueError:
                return None
            if isinstance(parsed, dict):
                return dict(parsed)
        return None

    @staticmethod
    def _serialize_vault_timestamp(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return str(value)

    @staticmethod
    def _json_safe_vault_value(
        value: Any,
        _seen: set[int] | None = None,
        *,
        _coerce_unknown: bool = True,
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
                    str(key): VaultService._json_safe_vault_value(item, _seen)
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
                    VaultService._json_safe_vault_value(item, _seen)
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
                return [
                    VaultService._json_safe_vault_value(item, _seen)
                    for item in value
                ]
            finally:
                _seen.remove(value_id)
        return str(value) if _coerce_unknown else value

    @staticmethod
    def _stringify_vault_content(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(
                VaultService._json_safe_vault_value(value, _coerce_unknown=False),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _coerce_vault_version_number(value: object) -> int:
        if isinstance(value, bool):
            return 0
        try:
            version_number = int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return version_number if version_number > 0 else 0

    @staticmethod
    def _coerce_vault_row_list(value: object) -> list[Any]:
        if isinstance(value, (list, tuple)):
            return list(value)
        return []

    @staticmethod
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
        text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
        text = _NAMED_SECRET_RE.sub(r"\1***", text)
        return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    async def create_project(
        self,
        user_id: uuid.UUID,
        name: str,
        language: str | None = None,
        framework: str | None = None,
    ) -> Project:
        """Create a new project in the vault."""
        project = Project(
            user_id=user_id,
            name=name,
            language=language,
            framework=framework,
        )
        self._db.add(project)
        await self._db.flush()
        logger.info("Created project %s for user %s", project.id, user_id)
        return project

    async def delete_project(
        self,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> None:
        """Delete a project and all its versions and exports.

        Only the owning user can delete a project.
        """
        project = await self._get_owned_project(user_id, project_id)

        # Delete child records first
        await self._db.execute(
            delete(ProjectVersion).where(ProjectVersion.project_id == project_id)
        )
        await self._db.execute(
            delete(Export).where(Export.project_id == project_id)
        )
        await self._db.delete(project)
        await self._db.flush()

        logger.info("Deleted project %s for user %s", project_id, user_id)

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    async def create_version(
        self,
        project_id: uuid.UUID,
        session_id: uuid.UUID | None,
        files_changed: list[str] | None,
        diff: str | None,
        commit_message: str | None,
        nfet_state: dict[str, Any] | None = None,
    ) -> ProjectVersion:
        """Create a new version snapshot of a project.

        Automatically increments the version number and updates the project's
        NFET state if provided.
        """
        # Determine next version number
        result = await self._db.execute(
            select(func.coalesce(func.max(ProjectVersion.version_number), 0))
            .where(ProjectVersion.project_id == project_id)
        )
        current_max = result.scalar_one()
        next_version = self._coerce_vault_version_number(current_max) + 1
        normalized_nfet_state = self._coerce_vault_mapping(nfet_state)

        version = ProjectVersion(
            project_id=project_id,
            session_id=session_id,
            version_number=next_version,
            commit_message=commit_message,
            files_changed=files_changed,
            diff=diff,
            nfet_state=normalized_nfet_state,
        )

        # Extract NFET metrics from state if present
        if normalized_nfet_state:
            version.nfet_phase = normalized_nfet_state.get("phase")
            version.es_score = normalized_nfet_state.get("es_score")

        self._db.add(version)

        # Update project counters and NFET state
        project = await self._db.get(Project, project_id)
        if project is not None:
            project.total_versions = next_version
            project.total_sessions = (project.total_sessions or 0) + (1 if session_id else 0)
            project.last_activity = datetime.utcnow()
            if normalized_nfet_state:
                project.latest_nfet_phase = normalized_nfet_state.get("phase")
                project.latest_es_score = normalized_nfet_state.get("es_score")

        await self._db.flush()
        logger.info(
            "Created version %d for project %s", next_version, project_id
        )
        return version

    async def get_project_versions(
        self,
        project_id: uuid.UUID,
    ) -> list[ProjectVersion]:
        """Retrieve all versions for a project, newest first."""
        result = await self._db.execute(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project_id)
            .order_by(ProjectVersion.version_number.desc())
        )
        return self._coerce_vault_row_list(result.scalars().all())

    async def restore_version(
        self,
        project_id: uuid.UUID,
        version_number: int,
    ) -> ProjectVersion:
        """Restore a project to a specific version.

        Creates a new version that is a copy of the target version's snapshot,
        effectively making it the latest state.
        """
        result = await self._db.execute(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project_id)
            .where(ProjectVersion.version_number == version_number)
        )
        target = result.scalar_one_or_none()
        if target is None:
            raise ValueError(
                f"Version {version_number} not found for project {project_id}"
            )

        # Create a restoration version
        restored = await self.create_version(
            project_id=project_id,
            session_id=None,
            files_changed=target.files_changed,
            diff=None,
            commit_message=f"Restored to version {version_number}",
            nfet_state=target.nfet_state,
        )
        restored.file_snapshot = target.file_snapshot
        await self._db.flush()

        logger.info(
            "Restored project %s to version %d (new version %d)",
            project_id, version_number, restored.version_number,
        )
        return restored

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------

    async def export_project(
        self,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        export_type: str,
        destination: str | None = None,
    ) -> Export:
        """Export a project in the requested format.

        Supported export_type values:
        - 'zip': ZIP archive of the latest file snapshot
        - 'github': Push to a GitHub repository (destination = repo URL)
        - 'json': Raw JSON dump of all versions
        """
        project = await self._get_owned_project(user_id, project_id)

        export_record = Export(
            user_id=user_id,
            project_id=project_id,
            export_type=export_type,
            destination=destination,
            status="processing",
        )
        self._db.add(export_record)
        await self._db.flush()

        try:
            if export_type == "zip":
                await self._export_zip(project, export_record)
            elif export_type == "github":
                await self._export_github(project, export_record, destination)
            elif export_type == "json":
                await self._export_json(project, export_record)
            else:
                export_record.status = "failed"
                export_record.error_message = f"Unsupported export type: {export_type}"
                await self._db.flush()
                return export_record

            export_record.status = "completed"
            export_record.completed_at = datetime.utcnow()
        except Exception as exc:
            safe_error = self._redact_vault_error(exc)
            logger.error(
                "Export failed for project %s: %s",
                self._redact_vault_error(project_id),
                safe_error,
            )
            export_record.status = "failed"
            export_record.error_message = safe_error

        await self._db.flush()
        return export_record

    async def get_exports(self, user_id: uuid.UUID) -> list[Export]:
        """Retrieve all exports for a user, newest first."""
        result = await self._db.execute(
            select(Export)
            .where(Export.user_id == user_id)
            .order_by(Export.created_at.desc())
        )
        return self._coerce_vault_row_list(result.scalars().all())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_owned_project(
        self,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> Project:
        """Fetch a project and verify ownership."""
        project = await self._db.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} not found")
        if project.user_id != user_id:
            raise PermissionError(
                f"User {user_id} does not own project {project_id}"
            )
        return project

    async def _export_zip(self, project: Project, export_record: Export) -> None:
        """Build a ZIP archive from the latest version's file snapshot."""
        versions = await self.get_project_versions(project.id)
        if not versions:
            raise ValueError("No versions to export")

        latest = versions[0]  # Already sorted newest first
        snapshot = self._coerce_vault_mapping(latest.file_snapshot) or {}
        if not snapshot:
            raise ValueError("No project files available for export")

        buf = io.BytesIO()
        seen_archive_paths: set[str] = set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for filepath, content in snapshot.items():
                zf.writestr(
                    dedupe_archive_path(
                        safe_archive_path(filepath),
                        seen_archive_paths,
                    ),
                    self._stringify_vault_content(content),
                )

        export_record.file_size_bytes = buf.tell()
        # In production, upload buf.getvalue() to S3 and set file_url.
        # For now, store a placeholder indicating the data is ready.
        export_record.metadata_ = {
            "format": "zip",
            "file_count": len(snapshot),
            "project_name": project.name,
        }

    async def _export_github(
        self,
        project: Project,
        export_record: Export,
        destination: str | None,
    ) -> None:
        """Push project to a GitHub repository.

        Requires the user's GitHub token (retrieved at the API layer).
        This records the intent — actual git operations happen asynchronously.
        """
        if not destination:
            raise ValueError("GitHub export requires a destination repository URL")

        export_record.metadata_ = {
            "format": "github",
            "repo_url": destination,
            "project_name": project.name,
            "status_detail": "queued_for_push",
        }

    async def _export_json(self, project: Project, export_record: Export) -> None:
        """Export all versions as a JSON dump."""
        versions = await self.get_project_versions(project.id)

        payload = {
            "project": {
                "id": str(project.id),
                "name": project.name,
                "language": project.language,
                "framework": project.framework,
            },
            "versions": [
                {
                    "version_number": v.version_number,
                    "commit_message": v.commit_message,
                    "files_changed": v.files_changed,
                    "nfet_phase": v.nfet_phase,
                    "es_score": v.es_score,
                    "created_at": self._serialize_vault_timestamp(v.created_at),
                }
                for v in versions
            ],
        }

        raw = json.dumps(
            self._json_safe_vault_value(payload),
            indent=2,
            default=str,
            allow_nan=False,
        )
        export_record.file_size_bytes = len(raw.encode())
        export_record.metadata_ = {
            "format": "json",
            "version_count": len(versions),
            "project_name": project.name,
        }
