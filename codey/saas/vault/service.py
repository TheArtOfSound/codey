"""Code Vault — project versioning, snapshot management, and export."""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.models.export import Export
from codey.saas.models.project import Project
from codey.saas.models.project_version import ProjectVersion

logger = logging.getLogger(__name__)


class VaultService:
    """Manages the Code Vault — versioned project storage with NFET state tracking."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

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
        next_version = current_max + 1

        version = ProjectVersion(
            project_id=project_id,
            session_id=session_id,
            version_number=next_version,
            commit_message=commit_message,
            files_changed=files_changed,
            diff=diff,
            nfet_state=nfet_state,
        )

        # Extract NFET metrics from state if present
        if nfet_state:
            version.nfet_phase = nfet_state.get("phase")
            version.es_score = nfet_state.get("es_score")

        self._db.add(version)

        # Update project counters and NFET state
        project = await self._db.get(Project, project_id)
        if project is not None:
            project.total_versions = next_version
            project.total_sessions = (project.total_sessions or 0) + (1 if session_id else 0)
            project.last_activity = datetime.now(timezone.utc)
            if nfet_state:
                project.latest_nfet_phase = nfet_state.get("phase")
                project.latest_es_score = nfet_state.get("es_score")

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
        return list(result.scalars().all())

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
            export_record.completed_at = datetime.now(timezone.utc)
        except Exception as exc:
            logger.exception("Export failed for project %s", project_id)
            export_record.status = "failed"
            export_record.error_message = str(exc)

        await self._db.flush()
        return export_record

    async def get_exports(self, user_id: uuid.UUID) -> list[Export]:
        """Retrieve all exports for a user, newest first."""
        result = await self._db.execute(
            select(Export)
            .where(Export.user_id == user_id)
            .order_by(Export.created_at.desc())
        )
        return list(result.scalars().all())

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
        snapshot = latest.file_snapshot or {}

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for filepath, content in snapshot.items():
                zf.writestr(filepath, content if isinstance(content, str) else json.dumps(content))

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
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                }
                for v in versions
            ],
        }

        raw = json.dumps(payload, indent=2)
        export_record.file_size_bytes = len(raw.encode())
        export_record.metadata_ = {
            "format": "json",
            "version_count": len(versions),
            "project_name": project.name,
        }
