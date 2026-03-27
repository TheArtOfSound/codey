from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.database import get_db
from codey.saas.models import Repository, User

router = APIRouter(prefix="/repos", tags=["repos"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ConnectRepoRequest(BaseModel):
    github_repo_url: str


class AutonomousModeRequest(BaseModel):
    enabled: bool
    config: dict[str, Any] | None = None


class RepoResponse(BaseModel):
    id: str
    full_name: str | None
    clone_url: str | None
    default_branch: str | None
    language: str | None
    autonomous_mode_enabled: bool
    autonomous_config: dict[str, Any] | None
    last_analyzed: str | None
    nfet_phase: str | None
    es_score: float | None
    created_at: str


class RepoHealthResponse(BaseModel):
    repo_id: str
    full_name: str | None
    nfet_phase: str | None
    es_score: float | None
    last_analyzed: str | None
    autonomous_mode_enabled: bool


class ActivityEntry(BaseModel):
    action: str
    timestamp: str
    details: dict[str, Any] | None


class RepoActivityResponse(BaseModel):
    repo_id: str
    entries: list[ActivityEntry]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_to_response(repo: Repository) -> RepoResponse:
    return RepoResponse(
        id=str(repo.id),
        full_name=repo.full_name,
        clone_url=repo.clone_url,
        default_branch=repo.default_branch,
        language=repo.language,
        autonomous_mode_enabled=repo.autonomous_mode_enabled,
        autonomous_config=repo.autonomous_config,
        last_analyzed=repo.last_analyzed.isoformat() if repo.last_analyzed else None,
        nfet_phase=repo.nfet_phase,
        es_score=repo.es_score,
        created_at=repo.created_at.isoformat(),
    )


def _parse_github_url(url: str) -> str:
    """Extract 'owner/repo' from a GitHub URL or pass-through if already in that form."""
    url = url.strip().rstrip("/")
    if url.startswith("https://github.com/"):
        path = url.removeprefix("https://github.com/")
    elif url.startswith("http://github.com/"):
        path = url.removeprefix("http://github.com/")
    elif "/" in url and not url.startswith("http"):
        path = url  # already "owner/repo"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
        )

    # Remove .git suffix and any trailing path segments
    path = path.removesuffix(".git")
    parts = path.split("/")
    if len(parts) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not parse owner/repo from URL",
        )
    return f"{parts[0]}/{parts[1]}"


async def _fetch_github_repo_info(full_name: str, token: str | None) -> dict:
    """Fetch repo metadata from the GitHub API."""
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{full_name}",
            headers=headers,
        )
        if resp.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"GitHub repository '{full_name}' not found or not accessible",
            )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[RepoResponse])
async def list_repos(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RepoResponse]:
    stmt = (
        select(Repository)
        .where(Repository.user_id == current_user.id)
        .order_by(Repository.created_at.desc())
    )
    result = await db.execute(stmt)
    repos = result.scalars().all()
    return [_repo_to_response(r) for r in repos]


@router.post("", response_model=RepoResponse, status_code=status.HTTP_201_CREATED)
async def connect_repo(
    body: ConnectRepoRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepoResponse:
    full_name = _parse_github_url(body.github_repo_url)

    # Check for duplicate
    existing_stmt = select(Repository).where(
        Repository.user_id == current_user.id,
        Repository.full_name == full_name,
    )
    existing_result = await db.execute(existing_stmt)
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Repository '{full_name}' is already connected",
        )

    # Fetch repo info from GitHub
    gh_data = await _fetch_github_repo_info(full_name, current_user.github_token)

    repo = Repository(
        user_id=current_user.id,
        github_repo_id=gh_data.get("id"),
        full_name=full_name,
        clone_url=gh_data.get("clone_url"),
        default_branch=gh_data.get("default_branch", "main"),
        language=gh_data.get("language"),
    )
    db.add(repo)
    await db.flush()

    return _repo_to_response(repo)


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_repo(
    repo_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        rid = uuid.UUID(repo_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository ID format",
        )

    stmt = select(Repository).where(
        Repository.id == rid,
        Repository.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    repo = result.scalar_one_or_none()

    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    await db.delete(repo)
    await db.flush()


@router.patch("/{repo_id}/autonomous", response_model=RepoResponse)
async def toggle_autonomous_mode(
    repo_id: str,
    body: AutonomousModeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepoResponse:
    try:
        rid = uuid.UUID(repo_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository ID format",
        )

    stmt = select(Repository).where(
        Repository.id == rid,
        Repository.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    repo = result.scalar_one_or_none()

    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    # Autonomous mode requires pro plan or above
    if body.enabled and not current_user.is_pro_or_above:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Autonomous mode requires the Pro plan or above",
        )

    repo.autonomous_mode_enabled = body.enabled
    if body.config is not None:
        repo.autonomous_config = body.config
    await db.flush()

    return _repo_to_response(repo)


@router.get("/{repo_id}/health", response_model=RepoHealthResponse)
async def get_repo_health(
    repo_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepoHealthResponse:
    try:
        rid = uuid.UUID(repo_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository ID format",
        )

    stmt = select(Repository).where(
        Repository.id == rid,
        Repository.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    repo = result.scalar_one_or_none()

    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    return RepoHealthResponse(
        repo_id=str(repo.id),
        full_name=repo.full_name,
        nfet_phase=repo.nfet_phase,
        es_score=repo.es_score,
        last_analyzed=repo.last_analyzed.isoformat() if repo.last_analyzed else None,
        autonomous_mode_enabled=repo.autonomous_mode_enabled,
    )


@router.get("/{repo_id}/activity", response_model=RepoActivityResponse)
async def get_repo_activity(
    repo_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepoActivityResponse:
    try:
        rid = uuid.UUID(repo_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository ID format",
        )

    stmt = select(Repository).where(
        Repository.id == rid,
        Repository.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    repo = result.scalar_one_or_none()

    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    # Pull autonomous action log from the repo's autonomous_config
    # In production this would come from a dedicated activity log table
    config = repo.autonomous_config or {}
    raw_log: list[dict] = config.get("activity_log", [])

    entries = [
        ActivityEntry(
            action=entry.get("action", "unknown"),
            timestamp=entry.get("timestamp", ""),
            details=entry.get("details"),
        )
        for entry in raw_log
    ]

    return RepoActivityResponse(
        repo_id=str(repo.id),
        entries=entries,
    )
