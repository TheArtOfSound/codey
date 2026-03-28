from __future__ import annotations

import base64
import json
import logging
import re
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
from codey.saas.intelligence import IntelligenceStack
from codey.saas.models import CodingSession, Repository, User
from codey.saas.sandbox import SandboxManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github", tags=["github"])

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
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


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
    repo = await _get_repo(repo_id, current_user, db)

    if not repo.full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository has no GitHub full_name set",
        )

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(
            f"{_GITHUB_API}/repos/{repo.full_name}/issues",
            params={
                "state": state,
                "per_page": min(per_page, 100),
                "page": page,
                "sort": "updated",
                "direction": "desc",
            },
            headers=_github_headers(current_user.github_token),
        )
        if resp.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"GitHub repo '{repo.full_name}' not found or not accessible",
            )
        resp.raise_for_status()
        raw_issues = resp.json()

    issues: list[IssueResponse] = []
    for issue in raw_issues:
        # Skip pull requests (GitHub API returns them as issues)
        if "pull_request" in issue:
            continue
        issues.append(
            IssueResponse(
                number=issue["number"],
                title=issue["title"],
                state=issue["state"],
                body=issue.get("body"),
                labels=[l["name"] for l in issue.get("labels", [])],
                assignee=issue["assignee"]["login"] if issue.get("assignee") else None,
                created_at=issue["created_at"],
                url=issue["html_url"],
            )
        )

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
    repo = await _get_repo(repo_id, current_user, db)

    if not repo.full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository has no GitHub full_name set",
        )

    # Fetch the issue details
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(
            f"{_GITHUB_API}/repos/{repo.full_name}/issues/{issue_number}",
            headers=_github_headers(current_user.github_token),
        )
        if resp.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Issue #{issue_number} not found",
            )
        resp.raise_for_status()
        issue_data = resp.json()

    # Fetch the repo tree to understand codebase structure
    tree_files = await _fetch_repo_tree(
        repo.full_name, repo.default_branch or "main", current_user.github_token
    )

    # Create a sandbox
    sandbox = await _sandbox_mgr.create(
        user_id=str(current_user.id),
        session_id=str(uuid.uuid4()),
    )

    # Clone relevant files into the sandbox
    relevant_files = await _identify_relevant_files(
        issue_data, tree_files, repo.language
    )
    for fpath in relevant_files[:20]:  # Limit to 20 files
        content = await _fetch_file_content(
            repo.full_name,
            fpath,
            repo.default_branch or "main",
            current_user.github_token,
        )
        if content is not None:
            await _sandbox_mgr.write_file(sandbox.id, fpath, content)

    # Run the intelligence stack to generate a fix
    issue_title = issue_data.get("title", "")
    issue_body = issue_data.get("body", "") or ""
    issue_labels = [l["name"] for l in issue_data.get("labels", [])]

    prompt = (
        f"Fix GitHub issue #{issue_number}: {issue_title}\n\n"
        f"Description:\n{issue_body[:3000]}\n\n"
        f"Labels: {', '.join(issue_labels)}\n\n"
        f"Repository: {repo.full_name} ({repo.language or 'unknown language'})\n"
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
            "language": repo.language or "python",
            "codebase_tokens": len(file_context.split()) * 2,
        },
    )

    # Extract modified files from the result
    files_modified: list[str] = []
    file_pattern = re.compile(
        r"(?:#{1,4}\s+`?([^\n`]+\.\w+)`?\s*\n```\w*\n(.*?)```)"
        r"|(?:```\w*\s*\n#\s*([\w/.-]+\.\w+)\n(.*?)```)",
        re.DOTALL,
    )
    for m in file_pattern.finditer(result.content):
        filename = m.group(1) or m.group(3)
        content = m.group(2) or m.group(4)
        if filename and content:
            filename = filename.strip()
            await _sandbox_mgr.write_file(sandbox.id, filename, content)
            files_modified.append(filename)

    # Create a coding session record
    session = CodingSession(
        user_id=current_user.id,
        mode="fix_issue",
        prompt=prompt[:2000],
        status="completed",
        output=result.content[:10000],
    )
    db.add(session)
    await db.flush()

    return FixIssueResponse(
        session_id=str(session.id),
        status="completed",
        plan=result.content[:2000],
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

    # Determine the repo from the session's prompt
    # In production, sessions would have a repo_id foreign key
    repo_stmt = (
        select(Repository)
        .where(Repository.user_id == current_user.id)
        .order_by(Repository.created_at.desc())
        .limit(1)
    )
    repo_result = await db.execute(repo_stmt)
    repo = repo_result.scalar_one_or_none()

    if repo is None or not repo.full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No connected repository found",
        )

    if not current_user.github_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GitHub authentication required to create PRs",
        )

    head_branch = body.head_branch or f"codey/fix-{session_uuid.hex[:8]}"

    # Create the branch and push files via GitHub API
    # Step 1: Get the base branch SHA
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        ref_resp = await client.get(
            f"{_GITHUB_API}/repos/{repo.full_name}/git/ref/heads/{body.base_branch}",
            headers=_github_headers(current_user.github_token),
        )
        if ref_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Base branch '{body.base_branch}' not found",
            )
        base_sha = ref_resp.json()["object"]["sha"]

        # Step 2: Create the new branch
        create_ref_resp = await client.post(
            f"{_GITHUB_API}/repos/{repo.full_name}/git/refs",
            json={
                "ref": f"refs/heads/{head_branch}",
                "sha": base_sha,
            },
            headers=_github_headers(current_user.github_token),
        )
        if create_ref_resp.status_code == 422:
            # Branch might already exist — that's ok
            logger.info("Branch %s already exists", head_branch)
        elif create_ref_resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create branch: {create_ref_resp.text}",
            )

        # Step 3: Push session output files via the Contents API
        output = session.output or ""
        file_pattern = re.compile(
            r"(?:#{1,4}\s+`?([^\n`]+\.\w+)`?\s*\n```\w*\n(.*?)```)"
            r"|(?:```\w*\s*\n#\s*([\w/.-]+\.\w+)\n(.*?)```)",
            re.DOTALL,
        )
        for m in file_pattern.finditer(output):
            filename = m.group(1) or m.group(3)
            content = m.group(2) or m.group(4)
            if not filename or not content:
                continue
            filename = filename.strip()

            # Check if file exists to get its SHA
            existing_resp = await client.get(
                f"{_GITHUB_API}/repos/{repo.full_name}/contents/{filename}",
                params={"ref": head_branch},
                headers=_github_headers(current_user.github_token),
            )
            file_sha = None
            if existing_resp.status_code == 200:
                file_sha = existing_resp.json().get("sha")

            # Create or update file
            put_body: dict[str, Any] = {
                "message": f"codey: update {filename}",
                "content": base64.b64encode(content.encode()).decode(),
                "branch": head_branch,
            }
            if file_sha:
                put_body["sha"] = file_sha

            put_resp = await client.put(
                f"{_GITHUB_API}/repos/{repo.full_name}/contents/{filename}",
                json=put_body,
                headers=_github_headers(current_user.github_token),
            )
            if put_resp.status_code not in (200, 201):
                logger.warning(
                    "Failed to push %s: %s", filename, put_resp.text[:200]
                )

        # Step 4: Create the PR
        pr_body = body.body or f"Automated fix by Codey AI\n\nSession: {body.session_id}"
        pr_resp = await client.post(
            f"{_GITHUB_API}/repos/{repo.full_name}/pulls",
            json={
                "title": body.title,
                "body": pr_body,
                "head": head_branch,
                "base": body.base_branch,
            },
            headers=_github_headers(current_user.github_token),
        )
        if pr_resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create PR: {pr_resp.text[:300]}",
            )

        pr_data = pr_resp.json()

    return CreatePRResponse(
        pr_number=pr_data["number"],
        url=pr_data["html_url"],
        title=pr_data["title"],
        state=pr_data["state"],
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
    repo = await _get_repo(repo_id, current_user, db)
    body = body or ReviewRequest()

    if not repo.full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository has no GitHub full_name set",
        )

    # Fetch PR diff
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        # Get PR details
        pr_resp = await client.get(
            f"{_GITHUB_API}/repos/{repo.full_name}/pulls/{pr_number}",
            headers=_github_headers(current_user.github_token),
        )
        if pr_resp.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"PR #{pr_number} not found",
            )
        pr_resp.raise_for_status()
        pr_data = pr_resp.json()

        # Get the diff
        diff_headers = _github_headers(current_user.github_token)
        diff_headers["Accept"] = "application/vnd.github.diff"
        diff_resp = await client.get(
            f"{_GITHUB_API}/repos/{repo.full_name}/pulls/{pr_number}",
            headers=diff_headers,
        )
        diff_resp.raise_for_status()
        diff_text = diff_resp.text

        # Get changed files
        files_resp = await client.get(
            f"{_GITHUB_API}/repos/{repo.full_name}/pulls/{pr_number}/files",
            params={"per_page": 100},
            headers=_github_headers(current_user.github_token),
        )
        files_resp.raise_for_status()
        changed_files = files_resp.json()

    # Build review prompt
    focus_instruction = ""
    if body.focus:
        focus_instruction = f"\nFocus especially on: {body.focus}\n"

    pr_title = pr_data.get("title", "")
    pr_body_text = pr_data.get("body", "") or ""

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
        context={"language": repo.language or "python"},
    )

    # Parse the AI response
    review = _parse_review_response(result.content)
    return review


def _parse_review_response(response: str) -> ReviewResponse:
    """Parse the AI's JSON review response, with fallback for malformed output."""
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

    comments: list[ReviewComment] = []
    for c in data.get("comments", []):
        comments.append(
            ReviewComment(
                path=c.get("path", ""),
                line=c.get("line"),
                body=c.get("body", ""),
                severity=c.get("severity", "suggestion"),
            )
        )

    return ReviewResponse(
        summary=data.get("summary", ""),
        score=float(data.get("score", 0.5)),
        comments=comments,
        approved=bool(data.get("approved", False)),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_repo_tree(
    full_name: str, branch: str, token: str | None
) -> list[str]:
    """Fetch the file tree of a repo from GitHub."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(
            f"{_GITHUB_API}/repos/{full_name}/git/trees/{branch}",
            params={"recursive": "1"},
            headers=_github_headers(token),
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            item["path"]
            for item in data.get("tree", [])
            if item.get("type") == "blob"
        ]


async def _fetch_file_content(
    full_name: str, path: str, branch: str, token: str | None
) -> str | None:
    """Fetch a single file's content from GitHub."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(
            f"{_GITHUB_API}/repos/{full_name}/contents/{path}",
            params={"ref": branch},
            headers=_github_headers(token),
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return data.get("content")


async def _identify_relevant_files(
    issue_data: dict,
    tree_files: list[str],
    language: str | None,
) -> list[str]:
    """Identify which files in the repo are most relevant to the issue."""
    title = (issue_data.get("title", "") or "").lower()
    body = (issue_data.get("body", "") or "").lower()
    labels = [l["name"].lower() for l in issue_data.get("labels", [])]
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
    allowed_exts = lang_extensions.get((language or "").lower(), _CODE_EXTENSIONS)

    # Score each file by keyword relevance
    scored: list[tuple[str, float]] = []
    for fpath in tree_files:
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
            if ("." + f.rsplit(".", 1)[-1] if "." in f else "") in allowed_exts
        ][:15]

    return [f for f, _ in scored[:20]]
