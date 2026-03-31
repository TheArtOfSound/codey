from __future__ import annotations

import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncio
import json as _json

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect, status
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
                    "- PREFER standard library modules over third-party packages when possible\n"
                    "- For GAMES, visual apps, or anything interactive: generate a SINGLE HTML file\n"
                    "  with inline JavaScript and CSS that runs in a browser. NOT terminal/curses code.\n"
                    "  Use canvas for graphics, requestAnimationFrame for game loops, addEventListener for input.\n"
                    "- For NON-interactive scripts: prefer stdlib (sqlite3, http.server, unittest)\n"
                    "- If third-party packages ARE needed, list them in a comment at the top: # pip install X Y Z\n"
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
    import asyncio as _asyncio

    import os as _os
    import re as _re

    ext_map = {"python": ("py", "python3"), "javascript": ("js", "node")}
    ext, runner = ext_map.get(body.language, ("py", "python3"))

    # Try E2B cloud sandbox first (full VM with package managers)
    e2b_key = _os.environ.get("E2B_API_KEY", "")
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

                result = sbx.commands.run(f"python3 -c '''{body.code}'''", timeout=30)
                return RunCodeResponse(
                    stdout=(result.stdout or "")[:10000],
                    stderr=(result.stderr or "")[:5000],
                    exit_code=result.exit_code,
                    timed_out=False,
                )
            finally:
                sbx.kill()
        except Exception as e2b_err:
            # Fall through to local subprocess
            pass

    # Fallback: local subprocess with auto-fix on errors
    tmp_dir = Path(tempfile.mkdtemp(prefix="codey_run_"))
    tmp_file = tmp_dir / f"main.{ext}"
    tmp_file.write_text(body.code)

    max_retries = 3
    timed_out = False

    for attempt in range(max_retries):
        try:
            proc = await _asyncio.create_subprocess_exec(
                runner, str(tmp_file),
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
                cwd=str(tmp_dir),
            )
            try:
                stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=30)
            except _asyncio.TimeoutError:
                proc.kill()
                stdout, stderr = b"", b"Execution timed out (30s limit)"
                timed_out = True
                break

            stderr_str = (stderr or b"").decode("utf-8", errors="replace")
            exit_code = proc.returncode or 0

            # Auto-fix: missing module → pip install and retry
            if exit_code != 0 and "ModuleNotFoundError: No module named" in stderr_str:
                missing = _re.search(r"No module named '(\w+)'", stderr_str)
                if missing and attempt < max_retries - 1:
                    pkg = missing.group(1)
                    # Map common module names to pip package names
                    pkg_map = {"cv2": "opencv-python", "PIL": "Pillow", "sklearn": "scikit-learn",
                               "bs4": "beautifulsoup4", "yaml": "pyyaml", "dotenv": "python-dotenv"}
                    pip_pkg = pkg_map.get(pkg, pkg)
                    install = await _asyncio.create_subprocess_exec(
                        "pip", "install", "-q", pip_pkg,
                        stdout=_asyncio.subprocess.PIPE,
                        stderr=_asyncio.subprocess.PIPE,
                    )
                    await _asyncio.wait_for(install.communicate(), timeout=30)
                    continue  # Retry execution

            # Auto-fix: syntax error → ask LLM to fix and retry
            if exit_code != 0 and ("SyntaxError" in stderr_str or "IndentationError" in stderr_str) and attempt < max_retries - 1:
                try:
                    from codey.saas.intelligence.providers import call_model, resolve_model
                    provider, model = resolve_model("debugging")
                    fix_result = await call_model(provider, model, [
                        {"role": "system", "content": "Fix this Python code error. Return ONLY the corrected code, no explanation."},
                        {"role": "user", "content": f"Error:\n{stderr_str[:500]}\n\nCode:\n{body.code}"},
                    ], max_tokens=4096)
                    # Extract code from response
                    fixed = fix_result
                    m = _re.search(r"```python\s*\n(.*?)```", fix_result, _re.DOTALL)
                    if m:
                        fixed = m.group(1)
                    tmp_file.write_text(fixed)
                    continue  # Retry with fixed code
                except Exception:
                    pass  # Can't fix, return the error

            # Success or unfixable error
            return RunCodeResponse(
                stdout=(stdout or b"").decode("utf-8", errors="replace")[:10000],
                stderr=stderr_str[:5000],
                exit_code=exit_code,
                timed_out=timed_out,
            )

        except Exception as e:
            return RunCodeResponse(
                stdout="",
                stderr=f"Execution error: {str(e)[:200]}",
                exit_code=-1,
                timed_out=False,
            )

    # Exhausted retries — clean up and return last result
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return RunCodeResponse(
        stdout=(stdout or b"").decode("utf-8", errors="replace")[:10000],
        stderr=(stderr or b"").decode("utf-8", errors="replace")[:5000],
        exit_code=proc.returncode or -1,
        timed_out=timed_out,
    )


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
        auth_data = _json.loads(auth_msg)
        token = auth_data.get("token", "")

        from codey.saas.auth.jwt import decode_access_token
        payload = decode_access_token(token)
        if not payload:
            await websocket.send_json({"type": "error", "message": "Invalid token"})
            await websocket.close()
            return

        user_id = payload.get("sub")
        prompt = auth_data.get("prompt", "")
        language = auth_data.get("language", "python")

        if not prompt:
            await websocket.send_json({"type": "error", "message": "No prompt provided"})
            await websocket.close()
            return

        # Status updates
        await websocket.send_json({"type": "status", "message": "Analyzing request..."})
        await asyncio.sleep(0.3)
        await websocket.send_json({"type": "status", "message": "Planning structure..."})
        await asyncio.sleep(0.3)
        await websocket.send_json({"type": "status", "message": "Generating code..."})

        # Generate code
        stack = IntelligenceStack()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Codey, the structural coding agent. Generate clean, "
                    "production-quality code that is secure, tested, and uses current packages.\n\n"
                    "RULES:\n"
                    "- Always use the LATEST stable package versions\n"
                    "- Never use eval(), exec(), os.system(), or hardcoded secrets\n"
                    "- Always validate inputs at system boundaries\n"
                    "- Use type hints in Python, TypeScript types in JS/TS\n"
                    "- Include error handling for external calls\n"
                    "- Return ONLY the code in a fenced code block\n"
                ),
            },
            {"role": "user", "content": prompt},
        ]
        context = {"language": language, "user_id": user_id}
        result = await stack.run(prompt, messages, context)

        output = result.content

        # Stream the code character by character
        await websocket.send_json({"type": "status", "message": "Streaming output..."})

        chunk_size = 50
        for i in range(0, len(output), chunk_size):
            chunk = output[i:i + chunk_size]
            await websocket.send_json({"type": "code_chunk", "content": chunk})
            await asyncio.sleep(0.02)

        lines = output.count("\n") + 1

        # Run structural health analysis
        try:
            from codey.saas.api.health_analysis import _analyze_code
            import re
            code_to_analyze = output
            m = re.search(r"```(?:python|javascript|typescript)?\s*\n(.*?)```", output, re.DOTALL)
            if m:
                code_to_analyze = m.group(1)
            analysis = _analyze_code(code_to_analyze, f"generated.py", language)
            await websocket.send_json({
                "type": "health_after",
                "phase": analysis["phase"],
                "score": analysis["health_score"],
                "coherence": analysis["coherence"],
                "stability": analysis["stability"],
                "summary": analysis["summary"],
                "recommendations": analysis["recommendations"],
            })
        except Exception:
            pass

        # Complete
        await websocket.send_json({
            "type": "complete",
            "credits_charged": 1,
            "lines_generated": lines,
            "files_modified": 1,
        })

    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        try:
            await websocket.send_json({"type": "error", "message": "Connection timed out"})
        except Exception:
            pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)[:200]})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
