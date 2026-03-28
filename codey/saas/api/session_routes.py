from __future__ import annotations

import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.credits.service import CreditService, InsufficientCreditsError, CREDIT_COSTS
from codey.saas.database import get_db
from codey.saas.intelligence import IntelligenceStack
from codey.saas.models import CodingSession, User
from codey.saas.sandbox.manager import SandboxManager

_sandbox_manager = SandboxManager()

router = APIRouter(prefix="/sessions", tags=["sessions"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PromptRequest(BaseModel):
    prompt: str
    language: str | None = None
    repo_id: str | None = None


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

    # 3. Create session record
    session = CodingSession(
        user_id=current_user.id,
        mode="prompt",
        prompt=body.prompt,
        repo_connected=body.repo_id,
        status="running",
        credits_charged=estimated,
        started_at=datetime.utcnow(),
    )
    db.add(session)
    await db.flush()

    # 4. Run the intelligence stack
    try:
        stack = IntelligenceStack()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Codey, the structural coding agent. You generate clean, "
                    "production-quality code that is secure, tested, and uses current packages.\n\n"
                    "RULES:\n"
                    "- Always use the LATEST stable package versions (check your knowledge)\n"
                    "- Never use eval(), exec(), os.system(), or hardcoded secrets\n"
                    "- Always validate inputs at system boundaries\n"
                    "- Use type hints in Python, TypeScript types in JS/TS\n"
                    "- Include error handling for external calls (API, DB, file I/O)\n"
                    "- Return ONLY the code in a fenced code block, no explanations unless asked\n"
                    "- If generating requirements.txt or package.json, pin exact versions\n"
                ),
            },
            {"role": "user", "content": body.prompt},
        ]
        context = {"language": body.language or "python", "user_id": str(current_user.id), "db": db}
        result = await stack.run(body.prompt, messages, context)

        output = result.content
        lines = output.count("\n") + 1

        # Extract security assessment if available
        sec_score = None
        sec_issues: list[str] = []
        if result.assessment:
            sec_score = result.assessment.score
            sec_issues = [
                i.message for i in result.assessment.issues
                if i.severity in ("error", "warning")
            ]

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
            health_report = HealthReport(
                phase=analysis["phase"],
                health_score=analysis["health_score"],
                coherence=analysis["coherence"],
                stability=analysis["stability"],
                total_nodes=analysis["total_nodes"],
                total_edges=analysis["total_edges"],
                summary=analysis["summary"],
                recommendations=analysis["recommendations"],
            )
        except Exception:
            pass  # Don't fail the response if analysis errors

        session.status = "completed"
        session.output_summary = output
        session.lines_generated = lines
        session.completed_at = datetime.utcnow()
        await db.flush()

        # Store session context as a memory for future retrieval
        try:
            from codey.saas.intelligence.embeddings import embedding_service
            memory_content = f"User asked: {body.prompt[:200]}. Generated {lines} lines of {body.language or 'python'} code."
            await embedding_service.store_memory(
                db,
                user_id=str(current_user.id),
                content=memory_content,
                memory_type="session_context",
                confidence=0.8,
            )
        except Exception:
            pass  # Memory storage is best-effort

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
        # Refund credits on failure
        session.status = "failed"
        session.error_message = str(e)
        session.completed_at = datetime.utcnow()
        session.credits_charged = 0
        await db.flush()
        # Attempt refund
        try:
            await credit_service.refund_credits(
                user_id=current_user.id,
                amount=estimated,
                description=f"Refund: session failed — {str(e)[:60]}",
            )
        except Exception:
            pass

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Code generation failed: {str(e)[:200]}",
        )


@router.post("/analyze", response_model=AnalyzeResponse, status_code=status.HTTP_201_CREATED)
async def create_analyze_session(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyzeResponse:
    cost = CREDIT_COSTS["file_analysis"]

    # 1. Reserve credits
    credit_service = CreditService(db)
    try:
        await credit_service.reserve_credits(
            user_id=current_user.id,
            estimated_cost=cost,
            description="File analysis session",
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

    # 2. Save uploaded files to a temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="codey_analyze_"))
    saved_paths: list[str] = []
    for upload in files:
        dest = temp_dir / (upload.filename or f"file_{uuid.uuid4().hex[:8]}")
        content = await upload.read()
        dest.write_bytes(content)
        saved_paths.append(str(dest))

    # 3. Create session record
    session = CodingSession(
        user_id=current_user.id,
        mode="analyze",
        files_uploaded=saved_paths,
        status="running",
        credits_charged=cost,
        started_at=datetime.utcnow(),
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
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    stmt = select(CodingSession).where(
        CodingSession.id == sid,
        CodingSession.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return SessionDetailResponse(
        id=str(session.id),
        user_id=str(session.user_id),
        mode=session.mode,
        prompt=session.prompt,
        files_uploaded=session.files_uploaded,
        repo_connected=session.repo_connected,
        status=session.status,
        credits_charged=session.credits_charged,
        lines_generated=session.lines_generated,
        files_modified=session.files_modified,
        nfet_phase_before=session.nfet_phase_before,
        nfet_phase_after=session.nfet_phase_after,
        es_score_before=session.es_score_before,
        es_score_after=session.es_score_after,
        output_summary=session.output_summary,
        error_message=session.error_message,
        started_at=session.started_at.isoformat(),
        completed_at=session.completed_at.isoformat() if session.completed_at else None,
    )


@router.post("/{session_id}/commit", response_model=CommitResponse)
async def commit_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommitResponse:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    stmt = select(CodingSession).where(
        CodingSession.id == sid,
        CodingSession.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Session is '{session.status}' — can only commit completed sessions",
        )

    # Charge 1 extra credit for the GitHub commit
    commit_cost = CREDIT_COSTS["github_commit"]
    credit_service = CreditService(db)
    try:
        await credit_service.reserve_credits(
            user_id=current_user.id,
            estimated_cost=commit_cost,
            description=f"GitHub commit for session {session_id[:8]}",
            session_id=sid,
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

    session.credits_charged += commit_cost
    await db.flush()

    # The actual git commit/PR creation would be triggered here asynchronously
    return CommitResponse(
        session_id=str(session.id),
        credits_charged=session.credits_charged,
        message="Commit initiated. Code will be pushed to the connected repository.",
    )


# ---------------------------------------------------------------------------
# Code execution (sandbox)
# ---------------------------------------------------------------------------


class RunCodeRequest(BaseModel):
    code: str
    language: str = "python"


class RunCodeResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


@router.post("/run", response_model=RunCodeResponse)
async def run_code(
    body: RunCodeRequest,
    current_user: User = Depends(get_current_user),
) -> RunCodeResponse:
    """Execute code in an isolated sandbox and return the output."""
    # Create sandbox
    sandbox = await _sandbox_manager.create(
        user_id=str(current_user.id),
        session_id="run-" + uuid.uuid4().hex[:8],
        timeout=30,
    )

    try:
        # Determine file extension and run command
        ext_map = {"python": ("py", "python3"), "javascript": ("js", "node"), "typescript": ("ts", "npx ts-node"), "go": ("go", "go run"), "rust": ("rs", "rustc -o /tmp/out && /tmp/out")}
        ext, runner = ext_map.get(body.language, ("py", "python3"))

        filename = f"main.{ext}"
        await _sandbox_manager.write_file(sandbox.id, filename, body.code)

        # Run the code
        if body.language == "rust":
            result = await _sandbox_manager.execute(sandbox.id, f"rustc {filename} -o /tmp/out && /tmp/out", timeout=30)
        else:
            result = await _sandbox_manager.execute(sandbox.id, f"{runner} {filename}", timeout=30)

        return RunCodeResponse(
            stdout=result.stdout[:10000],
            stderr=result.stderr[:5000],
            exit_code=result.exit_code,
            timed_out=result.timed_out,
        )
    finally:
        await _sandbox_manager.destroy(sandbox.id)
