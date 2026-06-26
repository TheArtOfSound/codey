from __future__ import annotations

import base64
import json
import logging
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.database import get_db
from codey.saas.intelligence import IntelligenceStack
from codey.saas.models import CodingSession, Repository, User, initialize_model_registry
from codey.saas.sandbox import SandboxManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github", tags=["github"])

initialize_model_registry()

_sandbox_mgr = SandboxManager()
_intelligence = IntelligenceStack()

_GITHUB_API = "https://api.github.com"
_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".swift", ".kt",
}
_CONFIG_EXTENSIONS = {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
}
_REVIEW_SEVERITIES = {"error", "warning", "suggestion", "praise"}
_GITHUB_REPO_FULL_NAME_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9._-]+"
)


def _normalize_branch_name(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith("refs/heads/"):
        normalized = normalized[len("refs/heads/"):]
    if (
        not normalized
        or normalized.startswith("/")
        or normalized.endswith("/")
        or normalized.startswith(".")
        or normalized.endswith(".")
        or "//" in normalized
        or ".." in normalized
        or "@{" in normalized
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
        or any(char in normalized for char in " ~^:?*[\\")
    ):
        raise ValueError("invalid branch name")

    parts = normalized.split("/")
    if any(not part or part.startswith(".") or part.endswith(".lock") for part in parts):
        raise ValueError("invalid branch name")

    return normalized


def _coerce_non_empty_github_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_github_bearer_token(value: Any) -> str | None:
    normalized = _coerce_non_empty_github_text(value)
    if (
        normalized is None
        or _has_ascii_control(normalized)
        or _has_whitespace(normalized)
    ):
        return None
    return normalized


def _coerce_github_repo_full_name(value: Any) -> str | None:
    full_name = _coerce_non_empty_github_text(value)
    if full_name is None or not _GITHUB_REPO_FULL_NAME_RE.fullmatch(full_name):
        return None
    repo_name = full_name.rsplit("/", 1)[1]
    if repo_name in {".", ".."}:
        return None
    return full_name


def _quote_github_path_segment(value: str) -> str:
    return quote(value, safe="")


def _quote_github_file_path(value: str) -> str:
    return quote(value, safe="/")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class IssueResponse(BaseModel):
    number: int
    title: str
    state: str
    body: str | None
    labels: list[str]
    assignee: str | None
    created_at: str
    url: str


class FixIssueRequest(BaseModel):
    branch_name: str | None = None
    auto_pr: bool = False

    @field_validator("branch_name")
    @classmethod
    def _normalize_optional_branch_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        return _normalize_branch_name(value)


class FixIssueResponse(BaseModel):
    session_id: str
    status: str
    plan: str
    files_modified: list[str]


class CreatePRRequest(BaseModel):
    session_id: str
    title: str
    body: str | None = None
    base_branch: str = "main"
    head_branch: str | None = None

    @field_validator("session_id")
    @classmethod
    def _strip_and_validate_session_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("title")
    @classmethod
    def _strip_and_validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("base_branch")
    @classmethod
    def _strip_and_validate_base_branch(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return _normalize_branch_name(value)

    @field_validator("head_branch")
    @classmethod
    def _normalize_optional_head_branch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        return _normalize_branch_name(value)

    @field_validator("body")
    @classmethod
    def _normalize_optional_body(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _validate_distinct_branches(self) -> CreatePRRequest:
        if self.head_branch and self.head_branch == self.base_branch:
            raise ValueError("head_branch must differ from base_branch")
        return self


class CreatePRResponse(BaseModel):
    pr_number: int
    url: str
    title: str
    state: str


class ReviewRequest(BaseModel):
    focus: str | None = None  # "security", "performance", "style", etc.


class ReviewComment(BaseModel):
    path: str
    line: int | None
    body: str
    severity: str  # "error", "warning", "suggestion", "praise"


class ReviewResponse(BaseModel):
    summary: str
    score: float
    comments: list[ReviewComment]
    approved: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _github_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = _coerce_github_bearer_token(token)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _raise_for_unexpected_httpx_status(response: Any, method: str = "GET") -> None:
    status_code = getattr(response, "status_code", None)
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
        return
    if isinstance(status_code, int) and status_code >= 400:
        request = httpx.Request(method, _GITHUB_API)
        raise httpx.HTTPStatusError(
            f"GitHub request failed with status {status_code}",
            request=request,
            response=httpx.Response(status_code, request=request),
        )


def _raise_github_access_or_upstream_error(detail: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=detail,
    )


def _normalize_repo_file_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    path_obj = PurePosixPath(normalized)
    if path_obj.is_absolute() or any(part.endswith(":") for part in path_obj.parts):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Session output contains an invalid file path: {path}",
        )

    parts = [
        part
        for part in path_obj.parts
        if part not in {"", ".", "/"}
    ]
    if (
        not parts
        or any(part == ".." for part in parts)
        or any(
            any(ord(char) < 32 or ord(char) == 127 for char in part)
            for part in parts
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Session output contains an invalid file path: {path}",
        )

    return PurePosixPath(*parts).as_posix()


def _extract_output_files(output: str) -> list[tuple[str, str]]:
    file_pattern = re.compile(
        r"(?:#{1,4}\s+`?([^\n`]+\.\w+)`?\s*\n```\w*\n(.*?)```)"
        r"|(?:```\w*\s*\n#\s*([\w/.-]+\.\w+)\n(.*?)```)",
        re.DOTALL,
    )
    files: list[tuple[str, str]] = []
    for match in file_pattern.finditer(output):
        filename = match.group(1) or match.group(3)
        content = match.group(2) or match.group(4)
        if not filename or not content:
            continue
        files.append((_normalize_repo_file_path(filename), content))
    return files


async def _get_repo(
    repo_id: str, user: User, db: AsyncSession
) -> Repository:
    """Retrieve a repo belonging to the user or raise 404."""
    try:
        rid = uuid.UUID(repo_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository ID format",
        )
    stmt = select(Repository).where(
        Repository.id == rid,
        Repository.user_id == user.id,
    )
    result = await db.execute(stmt)
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )
    return repo


def _invalid_github_issues_payload() -> None:
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="GitHub issues response was invalid. Try again.",
    )


def _extract_github_label_names(labels_raw: Any) -> list[str]:
    if not isinstance(labels_raw, list):
        return []
    return [
        name
        for label in labels_raw
        if isinstance(label, dict)
        for name in [_coerce_non_empty_github_text(label.get("name"))]
        if name
    ]


def _issue_to_response(issue: Any) -> IssueResponse | None:
    if not isinstance(issue, dict):
        _invalid_github_issues_payload()

    # GitHub's issues API includes pull requests in the same collection.
    if "pull_request" in issue:
        return None

    number = issue.get("number")
    title = _coerce_non_empty_github_text(issue.get("title"))
    state = _coerce_non_empty_github_text(issue.get("state"))
    created_at = _coerce_non_empty_github_text(issue.get("created_at"))
    url = _coerce_non_empty_github_text(issue.get("html_url"))
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or number < 1
        or not title
        or not state
        or not created_at
        or not url
    ):
        _invalid_github_issues_payload()

    labels_raw = issue.get("labels", [])
    if not isinstance(labels_raw, list):
        _invalid_github_issues_payload()
    labels = _extract_github_label_names(labels_raw)

    assignee_raw = issue.get("assignee")
    assignee = None
    if isinstance(assignee_raw, dict):
        assignee = _coerce_non_empty_github_text(assignee_raw.get("login"))

    body = issue.get("body")
    if body is not None and not isinstance(body, str):
        body = None
    else:
        body = _coerce_non_empty_github_text(body)

    return IssueResponse(
        number=number,
        title=title,
        state=state,
        body=body,
        labels=labels,
        assignee=assignee,
        created_at=created_at,
        url=url,
    )


# ---------------------------------------------------------------------------
# GET /github/issues/{repo_id} — list issues
# ---------------------------------------------------------------------------


@router.get("/issues/{repo_id}", response_model=list[IssueResponse])
async def list_issues(
    repo_id: str,
    state: str = "open",
    per_page: int = 30,
    page: int = 1,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[IssueResponse]:
    """List issues from a connected GitHub repository."""
    state_value = _coerce_non_empty_github_text(state)
    state = state_value.lower() if state_value else ""
    if state not in {"open", "closed", "all"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid issue state. Expected open, closed, or all.",
        )
    if (
        isinstance(per_page, bool)
        or isinstance(page, bool)
        or not isinstance(per_page, int)
        or not isinstance(page, int)
        or per_page < 1
        or page < 1
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="per_page and page must be positive integers",
        )

    repo = await _get_repo(repo_id, current_user, db)
    repo_full_name = _coerce_github_repo_full_name(
        getattr(repo, "full_name", None)
    )

    if not repo_full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository has no GitHub full_name set",
        )

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{repo_full_name}/issues",
                params={
                    "state": state,
                    "per_page": min(per_page, 100),
                    "page": page,
                    "sort": "updated",
                    "direction": "desc",
                },
                headers=_github_headers(getattr(current_user, "github_token", None)),
            )
            if resp.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"GitHub repo '{repo_full_name}' not found or not accessible",
                )
            if resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="GitHub denied repository access. Reconnect GitHub and try again.",
                )
            _raise_for_unexpected_httpx_status(resp)
            try:
                raw_issues = resp.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub issues response was invalid. Try again.",
                ) from exc
            if not isinstance(raw_issues, list):
                _invalid_github_issues_payload()
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="GitHub issues request timed out. Try again.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub issues request failed. Try again.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub issues request failed. Try again.",
        ) from exc

    issues: list[IssueResponse] = []
    for issue in raw_issues:
        parsed_issue = _issue_to_response(issue)
        if parsed_issue is not None:
            issues.append(parsed_issue)

    return issues


# ---------------------------------------------------------------------------
# POST /github/issues/{issue_id}/fix — AI fix for an issue
# ---------------------------------------------------------------------------


@router.post(
    "/issues/{repo_id}/{issue_number}/fix",
    response_model=FixIssueResponse,
)
async def fix_issue(
    repo_id: str,
    issue_number: int,
    body: FixIssueRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FixIssueResponse:
    """Start an AI session to fix a specific GitHub issue."""
    if (
        isinstance(issue_number, bool)
        or not isinstance(issue_number, int)
        or issue_number < 1
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="issue_number must be a positive integer",
        )

    repo = await _get_repo(repo_id, current_user, db)
    repo_full_name = _coerce_github_repo_full_name(
        getattr(repo, "full_name", None)
    )
    repo_language = _coerce_non_empty_github_text(
        getattr(repo, "language", None)
    )
    repo_default_branch = (
        _coerce_non_empty_github_text(getattr(repo, "default_branch", None))
        or "main"
    )
    github_token = _coerce_github_bearer_token(
        getattr(current_user, "github_token", None)
    )

    if not repo_full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository has no GitHub full_name set",
        )

    # Fetch the issue details
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{repo_full_name}/issues/{issue_number}",
                headers=_github_headers(github_token),
            )
            if resp.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Issue #{issue_number} not found",
                )
            if resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="GitHub denied repository access. Reconnect GitHub and try again.",
                )
            _raise_for_unexpected_httpx_status(resp)
            try:
                issue_data = resp.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub issue response was invalid. Try again.",
                ) from exc
            if not isinstance(issue_data, dict):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub issue response was invalid. Try again.",
                )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="GitHub issue request timed out. Try again.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub issue request failed. Try again.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub issue request failed. Try again.",
        ) from exc

    # Fetch the repo tree to understand codebase structure
    tree_files = await _fetch_repo_tree(
        repo_full_name, repo_default_branch, github_token
    )

    # Create a sandbox
    sandbox = await _sandbox_mgr.create(
        user_id=str(current_user.id),
        session_id=str(uuid.uuid4()),
    )

    # Clone relevant files into the sandbox
    relevant_files = await _identify_relevant_files(
        issue_data, tree_files, repo_language
    )
    for fpath in relevant_files[:20]:  # Limit to 20 files
        content = await _fetch_file_content(
            repo_full_name,
            fpath,
            repo_default_branch,
            github_token,
        )
        if content is not None:
            await _sandbox_mgr.write_file(sandbox.id, fpath, content)

    # Run the intelligence stack to generate a fix
    issue_title = _coerce_non_empty_github_text(issue_data.get("title")) or ""
    issue_body = _coerce_non_empty_github_text(issue_data.get("body")) or ""
    issue_labels = _extract_github_label_names(issue_data.get("labels", []))

    prompt = (
        f"Fix GitHub issue #{issue_number}: {issue_title}\n\n"
        f"Description:\n{issue_body[:3000]}\n\n"
        f"Labels: {', '.join(issue_labels)}\n\n"
        f"Repository: {repo_full_name} ({repo_language or 'unknown language'})\n"
        f"Relevant files:\n" + "\n".join(f"- {f}" for f in relevant_files[:20])
    )

    # Build context with file contents
    file_context = ""
    for fpath in relevant_files[:10]:
        try:
            content = await _sandbox_mgr.read_file(sandbox.id, fpath)
            file_context += f"\n--- {fpath} ---\n{content[:5000]}\n"
        except FileNotFoundError:
            pass

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior developer fixing a GitHub issue. "
                "Analyze the issue and the relevant code, then provide "
                "the fix. Output complete modified files with clear "
                "explanations of what you changed and why."
            ),
        },
        {"role": "user", "content": prompt + "\n\nFile contents:" + file_context},
    ]

    result = await _intelligence.run(
        request=prompt,
        messages=messages,
        context={
            "language": repo_language or "python",
            "codebase_tokens": len(file_context.split()) * 2,
        },
    )
    raw_result_content = getattr(result, "content", None)
    result_content = raw_result_content if isinstance(raw_result_content, str) else ""

    # Extract modified files from the result
    files_modified: list[str] = []
    file_pattern = re.compile(
        r"(?:#{1,4}\s+`?([^\n`]+\.\w+)`?\s*\n```\w*\n(.*?)```)"
        r"|(?:```\w*\s*\n#\s*([\w/.-]+\.\w+)\n(.*?)```)",
        re.DOTALL,
    )
    for m in file_pattern.finditer(result_content):
        filename = m.group(1) or m.group(3)
        content = m.group(2) or m.group(4)
        if filename and content:
            filename = _normalize_repo_file_path(filename)
            await _sandbox_mgr.write_file(sandbox.id, filename, content)
            files_modified.append(filename)

    # Create a coding session record
    session = CodingSession(
        user_id=current_user.id,
        mode="fix_issue",
        prompt=prompt[:2000],
        repo_connected=repo_full_name,
        status="completed",
        output_summary=result_content,
    )
    db.add(session)
    await db.flush()

    return FixIssueResponse(
        session_id=str(session.id),
        status="completed",
        plan=result_content[:2000],
        files_modified=files_modified,
    )


# ---------------------------------------------------------------------------
# POST /github/pr — create a pull request
# ---------------------------------------------------------------------------


@router.post("/pr", response_model=CreatePRResponse)
async def create_pull_request(
    body: CreatePRRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreatePRResponse:
    """Create a GitHub PR from a completed coding session's output."""
    # Find the session
    try:
        session_uuid = uuid.UUID(body.session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    stmt = select(CodingSession).where(
        CodingSession.id == session_uuid,
        CodingSession.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Prefer the repo captured on the session so PR creation does not drift to
    # the user's most recently connected repository.
    repo = None
    session_repo_full_name = _coerce_non_empty_github_text(
        getattr(session, "repo_connected", None)
    )
    if session_repo_full_name:
        repo_stmt = select(Repository).where(
            Repository.user_id == current_user.id,
            Repository.full_name == session_repo_full_name,
        )
        repo_result = await db.execute(repo_stmt)
        repo = repo_result.scalar_one_or_none()
        if repo is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The repository for this session is no longer connected",
            )

    # Fallback for older sessions that predate repo tracking.
    if repo is None:
        repo_stmt = (
            select(Repository)
            .where(Repository.user_id == current_user.id)
            .order_by(Repository.created_at.desc())
            .limit(1)
        )
        repo_result = await db.execute(repo_stmt)
        repo = repo_result.scalar_one_or_none()

    repo_full_name = _coerce_github_repo_full_name(getattr(repo, "full_name", None))
    if repo is None or not repo_full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No connected repository found",
        )

    github_token = _coerce_github_bearer_token(
        getattr(current_user, "github_token", None)
    )
    if not github_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GitHub authentication required to create PRs",
        )

    raw_output_summary = getattr(session, "output_summary", None)
    session_output_summary = (
        raw_output_summary if isinstance(raw_output_summary, str) else ""
    )
    output_files = _extract_output_files(session_output_summary)
    if not output_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session output does not contain any file changes to open as a PR",
        )

    head_branch = body.head_branch or f"codey/fix-{session_uuid.hex[:8]}"

    # Create the branch and push files via GitHub API
    # Step 1: Get the base branch SHA
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            ref_resp = await client.get(
                f"{_GITHUB_API}/repos/{repo_full_name}/git/ref/heads/"
                f"{_quote_github_path_segment(body.base_branch)}",
                headers=_github_headers(github_token),
            )
            if ref_resp.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Base branch '{body.base_branch}' not found",
                )
            if ref_resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="GitHub denied repository access. Reconnect GitHub and try again.",
                )
            if ref_resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub PR creation failed. Try again.",
                )
            try:
                ref_data = ref_resp.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub PR creation failed. Try again.",
                ) from exc
            if not isinstance(ref_data, dict):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub PR creation failed. Try again.",
                )
            object_data = ref_data.get("object")
            if not isinstance(object_data, dict):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub PR creation failed. Try again.",
                )
            base_sha = object_data.get("sha")
            if not isinstance(base_sha, str) or not base_sha:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub PR creation failed. Try again.",
                )

            # Step 2: Create the new branch
            create_ref_resp = await client.post(
                f"{_GITHUB_API}/repos/{repo_full_name}/git/refs",
                json={
                    "ref": f"refs/heads/{head_branch}",
                    "sha": base_sha,
                },
                headers=_github_headers(github_token),
            )
            if create_ref_resp.status_code == 422:
                # Branch might already exist — that's ok
                logger.info("Branch %s already exists", head_branch)
            elif create_ref_resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="GitHub denied repository access. Reconnect GitHub and try again.",
                )
            elif create_ref_resp.status_code not in (200, 201):
                _raise_github_access_or_upstream_error(
                    "GitHub PR creation failed. Try again."
                )

            # Step 3: Push session output files via the Contents API
            for filename, content in output_files:
                # Check if file exists to get its SHA
                existing_resp = await client.get(
                    f"{_GITHUB_API}/repos/{repo_full_name}/contents/"
                    f"{_quote_github_file_path(filename)}",
                    params={"ref": head_branch},
                    headers=_github_headers(github_token),
                )
                file_sha = None
                if existing_resp.status_code in {401, 403}:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="GitHub denied repository access. Reconnect GitHub and try again.",
                    )
                if existing_resp.status_code == 200:
                    try:
                        existing_data = existing_resp.json()
                    except ValueError as exc:
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="GitHub PR creation failed. Try again.",
                        ) from exc
                    if not isinstance(existing_data, dict):
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="GitHub PR creation failed. Try again.",
                        )
                    file_sha = existing_data.get("sha")
                    if not isinstance(file_sha, str) or not file_sha:
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="GitHub PR creation failed. Try again.",
                        )
                elif existing_resp.status_code != 404:
                    _raise_github_access_or_upstream_error(
                        "GitHub PR creation failed. Try again."
                    )

                # Create or update file
                put_body: dict[str, Any] = {
                    "message": f"codey: update {filename}",
                    "content": base64.b64encode(content.encode()).decode(),
                    "branch": head_branch,
                }
                if file_sha:
                    put_body["sha"] = file_sha

                put_resp = await client.put(
                    f"{_GITHUB_API}/repos/{repo_full_name}/contents/"
                    f"{_quote_github_file_path(filename)}",
                    json=put_body,
                    headers=_github_headers(github_token),
                )
                if put_resp.status_code in {401, 403}:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="GitHub denied repository access. Reconnect GitHub and try again.",
                    )
                if put_resp.status_code not in (200, 201):
                    logger.warning(
                        "Failed to push %s: %s", filename, put_resp.text[:200]
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="GitHub file upload failed. Try again.",
                    )

            # Step 4: Create the PR
            pr_body = body.body or f"Automated fix by Codey AI\n\nSession: {body.session_id}"
            pr_resp = await client.post(
                f"{_GITHUB_API}/repos/{repo_full_name}/pulls",
                json={
                    "title": body.title,
                    "body": pr_body,
                    "head": head_branch,
                    "base": body.base_branch,
                },
                headers=_github_headers(github_token),
            )
            if pr_resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="GitHub denied repository access. Reconnect GitHub and try again.",
                )
            if pr_resp.status_code not in (200, 201):
                _raise_github_access_or_upstream_error(
                    "GitHub PR creation failed. Try again."
                )
            try:
                pr_data = pr_resp.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub PR creation failed. Try again.",
                ) from exc
            if not isinstance(pr_data, dict):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub PR creation failed. Try again.",
                )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="GitHub PR creation timed out. Try again.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub PR creation failed. Try again.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub PR creation failed. Try again.",
        ) from exc

    pr_number_value = pr_data.get("number")
    pr_url = _coerce_non_empty_github_text(pr_data.get("html_url"))
    pr_title = _coerce_non_empty_github_text(pr_data.get("title"))
    pr_state = _coerce_non_empty_github_text(pr_data.get("state"))
    if (
        isinstance(pr_number_value, bool)
        or not isinstance(pr_number_value, int)
        or pr_number_value < 1
        or not pr_url
        or not pr_title
        or not pr_state
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub PR creation failed. Try again.",
        )

    return CreatePRResponse(
        pr_number=pr_number_value,
        url=pr_url,
        title=pr_title,
        state=pr_state,
    )


# ---------------------------------------------------------------------------
# POST /github/review/{repo_id}/{pr_number} — AI code review
# ---------------------------------------------------------------------------


@router.post(
    "/review/{repo_id}/{pr_number}",
    response_model=ReviewResponse,
)
async def review_pr(
    repo_id: str,
    pr_number: int,
    body: ReviewRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    """Run an AI code review on a pull request."""
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pr_number must be a positive integer",
        )

    repo = await _get_repo(repo_id, current_user, db)
    body = body or ReviewRequest()

    repo_full_name = _coerce_github_repo_full_name(
        getattr(repo, "full_name", None)
    )
    repo_language = _coerce_non_empty_github_text(
        getattr(repo, "language", None)
    )
    github_token = _coerce_github_bearer_token(
        getattr(current_user, "github_token", None)
    )
    if not repo_full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository has no GitHub full_name set",
        )

    # Fetch PR diff
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            # Get PR details
            pr_resp = await client.get(
                f"{_GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}",
                headers=_github_headers(github_token),
            )
            if pr_resp.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"PR #{pr_number} not found",
                )
            if pr_resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="GitHub denied repository access. Reconnect GitHub and try again.",
                )
            _raise_for_unexpected_httpx_status(pr_resp)
            try:
                pr_data = pr_resp.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub PR response was invalid. Try again.",
                ) from exc
            if not isinstance(pr_data, dict):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub PR response was invalid. Try again.",
                )

            # Get the diff
            diff_headers = _github_headers(github_token)
            diff_headers["Accept"] = "application/vnd.github.diff"
            diff_resp = await client.get(
                f"{_GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}",
                headers=diff_headers,
            )
            if diff_resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="GitHub denied repository access. Reconnect GitHub and try again.",
                )
            _raise_for_unexpected_httpx_status(diff_resp)
            diff_text = diff_resp.text

            # Get changed files
            files_resp = await client.get(
                f"{_GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}/files",
                params={"per_page": 100},
                headers=_github_headers(github_token),
            )
            if files_resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="GitHub denied repository access. Reconnect GitHub and try again.",
                )
            _raise_for_unexpected_httpx_status(files_resp)
            try:
                changed_files = files_resp.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub PR response was invalid. Try again.",
                ) from exc
            if not isinstance(changed_files, list):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub PR response was invalid. Try again.",
                )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="GitHub PR request timed out. Try again.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub PR request failed. Try again.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub PR request failed. Try again.",
        ) from exc

    # Build review prompt
    focus_instruction = ""
    if body.focus:
        focus_instruction = f"\nFocus especially on: {body.focus}\n"

    pr_title = _coerce_non_empty_github_text(pr_data.get("title")) or ""
    pr_body_text = _coerce_non_empty_github_text(pr_data.get("body")) or ""

    prompt = (
        f"Review this pull request.\n\n"
        f"PR #{pr_number}: {pr_title}\n"
        f"Description: {pr_body_text[:1000]}\n"
        f"Changed files: {len(changed_files)}\n"
        f"{focus_instruction}\n"
        f"Diff:\n```diff\n{diff_text[:15000]}\n```\n\n"
        "Provide a structured review with:\n"
        "1. A summary of the changes\n"
        "2. A quality score from 0.0 to 1.0\n"
        "3. Specific comments on issues found (with file path, line number, severity)\n"
        "4. Whether you would approve the PR\n\n"
        'Output as JSON with keys: summary, score, comments (array of {path, line, body, severity}), approved'
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert code reviewer. Analyze the PR diff and provide "
                "detailed, actionable feedback. Be thorough but fair. Look for bugs, "
                "security issues, performance problems, and style violations. "
                "Output your review as valid JSON."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    result = await _intelligence.run(
        request=prompt,
        messages=messages,
        context={"language": repo_language or "python"},
    )

    # Parse the AI response
    raw_review_content = getattr(result, "content", None)
    review_content = raw_review_content if isinstance(raw_review_content, str) else ""
    review = _parse_review_response(review_content)
    return review


def _parse_review_response(response: str) -> ReviewResponse:
    """Parse the AI's JSON review response, with fallback for malformed output."""
    def _coerce_review_string(value: Any, fallback: str = "") -> str:
        if not isinstance(value, str):
            return fallback
        candidate = value.strip()
        return candidate if candidate else fallback

    def _coerce_review_bool(value: Any, fallback: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            if value == 1:
                return True
            if value == 0:
                return False
            return fallback
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "1"}:
                return True
            if normalized in {"false", "no", "0", ""}:
                return False
        return fallback

    # Try to extract JSON
    json_match = re.search(r"```(?:json)?\s*\n(.*?)```", response, re.DOTALL)
    raw = json_match.group(1) if json_match else response

    # Find JSON object
    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        raw = raw[brace_start : brace_end + 1]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: treat the whole response as the summary
        return ReviewResponse(
            summary=response[:1000],
            score=0.5,
            comments=[],
            approved=False,
        )
    if not isinstance(data, dict):
        return ReviewResponse(
            summary=response[:1000],
            score=0.5,
            comments=[],
            approved=False,
        )

    comments: list[ReviewComment] = []
    raw_comments = data.get("comments", [])
    if isinstance(raw_comments, list):
        for c in raw_comments:
            if not isinstance(c, dict):
                continue
            line = c.get("line")
            if line is not None:
                if isinstance(line, bool):
                    line = None
                else:
                    try:
                        line = int(line)
                    except (TypeError, ValueError, OverflowError):
                        line = None
                    if isinstance(line, int) and line <= 0:
                        line = None
            severity = _coerce_review_string(c.get("severity"), "suggestion").lower()
            if severity not in _REVIEW_SEVERITIES:
                severity = "suggestion"
            comments.append(
                ReviewComment(
                    path=_coerce_review_string(c.get("path")),
                    line=line,
                    body=_coerce_review_string(c.get("body")),
                    severity=severity,
                )
            )

    try:
        score = float(data.get("score", 0.5))
    except (TypeError, ValueError, OverflowError):
        score = 0.5
    if not math.isfinite(score):
        score = 0.5
    score = max(0.0, min(score, 1.0))

    return ReviewResponse(
        summary=_coerce_review_string(data.get("summary")),
        score=score,
        comments=comments,
        approved=_coerce_review_bool(data.get("approved", False)),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_repo_tree(
    full_name: str, branch: str, token: str | None
) -> list[str]:
    """Fetch the file tree of a repo from GitHub."""
    full_name = _coerce_github_repo_full_name(full_name)
    branch = _coerce_non_empty_github_text(branch)
    if not full_name or not branch:
        return []
    try:
        branch = _normalize_branch_name(branch)
    except ValueError:
        return []

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{full_name}/git/trees/"
                f"{_quote_github_path_segment(branch)}",
                params={"recursive": "1"},
                headers=_github_headers(token),
            )
            if resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="GitHub denied repository access. Reconnect GitHub and try again.",
                )
            if resp.status_code != 200:
                return []
            try:
                data = resp.json()
            except ValueError:
                return []
            if not isinstance(data, dict):
                return []
            tree = data.get("tree", [])
            if not isinstance(tree, list):
                return []
            return [
                item["path"]
                for item in tree
                if isinstance(item, dict)
                and item.get("type") == "blob"
                and isinstance(item.get("path"), str)
            ]
    except (httpx.TimeoutException, httpx.RequestError):
        return []


async def _fetch_file_content(
    full_name: str, path: str, branch: str, token: str | None
) -> str | None:
    """Fetch a single file's content from GitHub."""
    full_name = _coerce_github_repo_full_name(full_name)
    path = _coerce_non_empty_github_text(path)
    branch = _coerce_non_empty_github_text(branch)
    if not full_name or not path or not branch:
        return None
    try:
        path = _normalize_repo_file_path(path)
        branch = _normalize_branch_name(branch)
    except (HTTPException, ValueError):
        return None

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{full_name}/contents/"
                f"{_quote_github_file_path(path)}",
                params={"ref": branch},
                headers=_github_headers(token),
            )
            if resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="GitHub denied repository access. Reconnect GitHub and try again.",
                )
            if resp.status_code != 200:
                return None
            try:
                data = resp.json()
            except ValueError:
                return None
            if not isinstance(data, dict):
                return None
            content = data.get("content")
            if data.get("encoding") == "base64":
                if not isinstance(content, str):
                    return None
                try:
                    return base64.b64decode(content).decode("utf-8", errors="replace")
                except (ValueError, TypeError):
                    return None
            return content if isinstance(content, str) else None
    except (httpx.TimeoutException, httpx.RequestError):
        return None


async def _identify_relevant_files(
    issue_data: dict,
    tree_files: list[str],
    language: str | None,
) -> list[str]:
    """Identify which files in the repo are most relevant to the issue."""
    title_raw = issue_data.get("title", "")
    body_raw = issue_data.get("body", "")
    title = title_raw.lower() if isinstance(title_raw, str) else ""
    body = body_raw.lower() if isinstance(body_raw, str) else ""

    raw_labels = issue_data.get("labels", [])
    labels: list[str] = []
    if isinstance(raw_labels, list):
        for label in raw_labels:
            if not isinstance(label, dict):
                continue
            name = label.get("name")
            if isinstance(name, str) and name:
                labels.append(name.lower())
    combined_text = f"{title} {body} {' '.join(labels)}"

    # Extension filter by language
    lang_extensions: dict[str, set[str]] = {
        "python": {".py"},
        "javascript": {".js", ".jsx", ".ts", ".tsx"},
        "typescript": {".ts", ".tsx", ".js", ".jsx"},
        "rust": {".rs"},
        "go": {".go"},
        "java": {".java"},
        "ruby": {".rb"},
    }
    language_key = (_coerce_non_empty_github_text(language) or "").lower()
    allowed_exts = lang_extensions.get(language_key, _CODE_EXTENSIONS)

    # Score each file by keyword relevance
    scored: list[tuple[str, float]] = []
    for fpath in tree_files:
        if not isinstance(fpath, str) or not fpath:
            continue
        ext = "." + fpath.rsplit(".", 1)[-1] if "." in fpath else ""
        if ext not in allowed_exts and ext not in _CONFIG_EXTENSIONS:
            continue
        # Skip test files for now unless the issue is about tests
        if "test" in fpath.lower() and "test" not in combined_text:
            continue

        score = 0.0
        path_lower = fpath.lower()
        parts = set(re.split(r"[/._-]", path_lower))

        for word in combined_text.split():
            if len(word) > 2 and word in path_lower:
                score += 2.0
            if word in parts:
                score += 1.0

        # Boost important files
        if any(k in path_lower for k in ["route", "api", "endpoint", "view"]):
            score += 0.5
        if any(k in path_lower for k in ["model", "schema", "migration"]):
            score += 0.5
        if path_lower.endswith("__init__.py") or path_lower.endswith("index.ts"):
            score += 0.3

        if score > 0:
            scored.append((fpath, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    # If no matches, return top-level source files
    if not scored:
        return [
            f for f in tree_files
            if isinstance(f, str)
            and f
            and ("." + f.rsplit(".", 1)[-1] if "." in f else "") in allowed_exts
        ][:15]

    return [f for f, _ in scored[:20]]
