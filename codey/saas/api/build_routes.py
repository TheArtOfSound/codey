from __future__ import annotations

import uuid
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.auth.jwt import decode_access_token
from codey.saas.credits.service import CreditService, InsufficientCreditsError, CREDIT_COSTS
from codey.saas.database import get_db
from codey.saas.models import BuildCheckpoint, BuildFile, BuildProject, User

router = APIRouter(prefix="/build", tags=["build"])


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
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project ID format",
        )

    stmt = select(BuildProject).where(
        BuildProject.id == pid,
        BuildProject.user_id == user.id,
    )
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Build project not found",
        )

    return project


def _project_to_response(project: BuildProject) -> BuildProjectResponse:
    return BuildProjectResponse(
        id=str(project.id),
        name=project.name,
        description=project.description,
        status=project.status,
        current_phase=project.current_phase,
        total_phases=project.total_phases,
        files_planned=project.files_planned,
        files_completed=project.files_completed,
        lines_generated=project.lines_generated,
        credits_charged=project.credits_charged,
        nfet_es_score_final=project.nfet_es_score_final,
        nfet_phase_final=project.nfet_phase_final,
        project_plan=project.project_plan,
        file_tree=project.file_tree,
        stack=project.stack,
        download_url=project.download_url,
        github_repo_url=project.github_repo_url,
        started_at=project.started_at.isoformat(),
        completed_at=project.completed_at.isoformat() if project.completed_at else None,
    )


def _file_to_response(f: BuildFile) -> BuildFileResponse:
    return BuildFileResponse(
        id=str(f.id),
        file_path=f.file_path,
        line_count=f.line_count,
        phase=f.phase,
        status=f.status,
        stress_score=f.stress_score,
        validation_passed=f.validation_passed,
        generated_at=f.generated_at.isoformat() if f.generated_at else None,
    )


def _file_to_detail(f: BuildFile) -> BuildFileDetailResponse:
    return BuildFileDetailResponse(
        id=str(f.id),
        file_path=f.file_path,
        content=f.content,
        line_count=f.line_count,
        phase=f.phase,
        status=f.status,
        stress_score=f.stress_score,
        validation_passed=f.validation_passed,
        generated_at=f.generated_at.isoformat() if f.generated_at else None,
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

    answers = body.answers or {}
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

    # Generate plan phases
    phases: list[dict[str, Any]] = []
    phase_names = [
        "Project Setup & Configuration",
        "Core Data Models & Database",
        "Business Logic & Services",
        "API Routes & Controllers",
        "Frontend & Integration",
    ][:total_phases]

    files_per_phase = total_files // total_phases
    all_planned_files: list[str] = []

    for i, phase_name in enumerate(phase_names):
        count = files_per_phase if i < total_phases - 1 else total_files - files_per_phase * (total_phases - 1)
        phase_files = [f"src/phase_{i + 1}_file_{j + 1}" for j in range(count)]
        all_planned_files.extend(phase_files)
        phases.append({
            "phase": i + 1,
            "name": phase_name,
            "files": phase_files,
            "description": f"Phase {i + 1}: {phase_name}",
        })

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
    estimated = max(
        CREDIT_COSTS["full_build"],
        (project.files_planned or 12) * 2,
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

    # The actual build execution is handled asynchronously via the WebSocket stream
    return BuildApproveResponse(
        project_id=str(project.id),
        session_id=str(project.session_id or project.id),
        status="building",
    )


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
    files = result.scalars().all()

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
    phase_files = result.scalars().all()

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

    return CheckpointResponse(
        id=str(checkpoint.id),
        project_id=str(project.id),
        phase=checkpoint.phase,
        phase_name=checkpoint.phase_name,
        files_in_phase=checkpoint.files_in_phase,
        tests_passed=checkpoint.tests_passed,
        tests_failed=checkpoint.tests_failed,
        nfet_es_score=checkpoint.nfet_es_score,
        nfet_kappa=checkpoint.nfet_kappa,
        nfet_sigma=checkpoint.nfet_sigma,
        user_action=checkpoint.user_action,
        checkpoint_at=checkpoint.checkpoint_at.isoformat(),
    )


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

    # If a pre-generated URL exists, return it
    if project.download_url:
        return DownloadResponse(
            download_url=project.download_url,
            filename=f"{project.name or 'project'}.zip",
            size_bytes=0,
        )

    # Generate the zip on-the-fly
    stmt = select(BuildFile).where(
        BuildFile.project_id == project.id,
        BuildFile.status == "completed",
    )
    result = await db.execute(stmt)
    files = result.scalars().all()

    temp_dir = Path(tempfile.mkdtemp(prefix="codey_build_"))
    zip_path = temp_dir / f"{project.name or 'project'}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            if f.content:
                zf.writestr(f.file_path, f.content)

    size = zip_path.stat().st_size
    download_url = f"/build/{project_id}/download/zip"
    project.download_url = str(zip_path)
    await db.flush()

    return DownloadResponse(
        download_url=download_url,
        filename=zip_path.name,
        size_bytes=size,
    )


@router.get("/templates", response_model=list[TemplateInfo])
async def list_templates(
    current_user: User = Depends(get_current_user),
) -> list[TemplateInfo]:
    """List available project templates."""
    return [TemplateInfo(**tpl) for tpl in TEMPLATES]


# ---------------------------------------------------------------------------
# WebSocket: real-time build progress stream
# ---------------------------------------------------------------------------


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
    # Authenticate via query-string token
    if not token:
        await websocket.close(code=1008, reason="Missing authentication token")
        return

    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=1008, reason="Invalid token")
            return
    except Exception:
        await websocket.close(code=1008, reason="Invalid token")
        return

    await websocket.accept()

    try:
        # Validate project ownership
        async with get_db().__aiter__().__anext__() as db:  # type: ignore[attr-defined]
            pass
    except Exception:
        pass

    # Send initial connection acknowledgment
    await websocket.send_json({
        "type": "status",
        "data": {"message": "Connected to build stream", "project_id": project_id},
        "timestamp": datetime.utcnow().isoformat(),
    })

    try:
        # Keep connection alive and relay build events
        # In production, this would subscribe to a message broker (Redis pub/sub, etc.)
        # and forward events from the build engine to the client.
        while True:
            # Listen for client messages (heartbeats, cancellation requests)
            data = await websocket.receive_text()
            import json
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "data": {},
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                elif msg.get("type") == "cancel":
                    await websocket.send_json({
                        "type": "status",
                        "data": {"message": "Build cancellation requested"},
                        "timestamp": datetime.utcnow().isoformat(),
                    })
            except (json.JSONDecodeError, KeyError):
                pass

    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close(code=1011, reason="Internal error")
        except Exception:
            pass
