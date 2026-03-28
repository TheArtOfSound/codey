"""Structural health analysis — wires parser + graph engine + NFET sweep into API endpoints."""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.database import get_db
from codey.saas.models import User

router = APIRouter(tags=["health"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AnalyzeCodeRequest(BaseModel):
    code: str
    filename: str = "main.py"
    language: str = "python"


class ComponentHealth(BaseModel):
    name: str
    file_path: str
    stress: float
    coupling: float
    cohesion: float
    cascade_depth: int


class HealthReport(BaseModel):
    phase: str  # "Excellent" | "Watch this" | "Needs attention"
    health_score: float
    coherence: float  # kappa (coupling density)
    stability: float  # sigma (cascade margin)
    total_nodes: int
    total_edges: int
    mean_coupling: float
    mean_cohesion: float
    highest_stress_component: str
    highest_stress_value: float
    top_components: list[ComponentHealth]
    summary: str  # Plain language summary


class AnalyzeCodeResponse(BaseModel):
    report: HealthReport
    recommendations: list[str]


# ---------------------------------------------------------------------------
# Analysis engine
# ---------------------------------------------------------------------------


def _analyze_code(code: str, filename: str, language: str) -> dict[str, Any]:
    """Parse code, build graph, run NFET sweep, return results."""
    from codey.parser.extractor import extract_from_source
    from codey.graph.engine import CodebaseGraph
    from codey.nfet.sweep import NFETSweep, Phase

    # Parse the code into nodes and edges
    nodes, edges = extract_from_source(code, filename, language)

    # Build the graph
    graph = CodebaseGraph()
    graph.build_from_nodes_edges(nodes, edges)

    # Run the NFET sweep
    sweep = NFETSweep(
        alpha=1.0,
        beta=2.0,
        sigma_star=0.30,
        kappa_star=0.45,
        kappa_max=1.0,
    )
    result = sweep.run(graph)

    # Map phase to plain language
    phase_labels = {
        Phase.RIDGE: "Excellent",
        Phase.CAUTION: "Watch this",
        Phase.CRITICAL: "Needs attention",
    }

    # Get top stress components with details
    top_components = []
    for nid, stress_val in result.top_stress_components[:5]:
        node_data = graph._graph.nodes.get(nid, {})
        top_components.append({
            "name": node_data.get("name", nid),
            "file_path": node_data.get("file_path", filename),
            "stress": round(stress_val, 4),
            "coupling": round(graph.coupling_score(nid), 4),
            "cohesion": round(graph.cohesion_score(node_data.get("file_path", "")), 4),
            "cascade_depth": graph.cascade_depth(nid),
        })

    # Generate plain language summary
    es = result.es_score
    if es >= 0.7:
        summary = (
            f"Your code is structurally healthy. "
            f"{result.total_nodes} components with safe stability margins. "
            f"No cascade risks detected."
        )
    elif es >= 0.4:
        summary = (
            f"Your code has some structural concerns. "
            f"Component '{result.highest_stress_component}' is carrying "
            f"high coupling relative to its cohesion. "
            f"Consider refactoring before adding more dependencies."
        )
    else:
        summary = (
            f"Your code has critical structural issues. "
            f"Component '{result.highest_stress_component}' has crossed "
            f"the cascade threshold — a failure here could propagate widely. "
            f"Immediate refactoring recommended."
        )

    # Generate recommendations
    recommendations = []
    if result.kappa > 0.6:
        recommendations.append(
            "High coupling density. Consider extracting shared logic into utility modules."
        )
    if result.sigma < 0.3:
        recommendations.append(
            "Low stability margin. The highest-stress component is close to cascade risk."
        )
    if result.mean_cohesion < 0.4:
        recommendations.append(
            "Low module cohesion. Components have too many external dependencies relative to internal ones."
        )
    for nid, stress_val in result.top_stress_components[:3]:
        if stress_val > 0.7:
            name = graph._graph.nodes.get(nid, {}).get("name", nid)
            recommendations.append(
                f"Refactor '{name}' — stress score {stress_val:.2f} indicates fragility."
            )
    if not recommendations:
        recommendations.append("No structural issues detected. Safe to add features.")

    return {
        "phase": phase_labels.get(result.phase, "Unknown"),
        "health_score": round(result.es_score, 4),
        "coherence": round(result.kappa, 4),
        "stability": round(result.sigma, 4),
        "total_nodes": result.total_nodes,
        "total_edges": result.total_edges,
        "mean_coupling": round(result.mean_coupling, 4),
        "mean_cohesion": round(result.mean_cohesion, 4),
        "highest_stress_component": result.highest_stress_component,
        "highest_stress_value": round(result.highest_stress_value, 4),
        "top_components": top_components,
        "summary": summary,
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/analyze/code", response_model=AnalyzeCodeResponse)
async def analyze_code(
    body: AnalyzeCodeRequest,
    current_user: User = Depends(get_current_user),
) -> AnalyzeCodeResponse:
    """Analyze a code snippet and return a structural health report."""
    try:
        result = _analyze_code(body.code, body.filename, body.language)
        return AnalyzeCodeResponse(
            report=HealthReport(**{k: v for k, v in result.items() if k != "recommendations"}),
            recommendations=result["recommendations"],
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)[:200]}",
        )


@router.post("/analyze/upload", response_model=AnalyzeCodeResponse)
async def analyze_upload(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
) -> AnalyzeCodeResponse:
    """Upload files and get a structural health report."""
    from codey.parser.extractor import extract_from_directory
    from codey.graph.engine import CodebaseGraph
    from codey.nfet.sweep import NFETSweep, Phase

    # Save uploaded files to temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="codey_analyze_"))
    try:
        for upload in files:
            dest = temp_dir / (upload.filename or f"file_{uuid.uuid4().hex[:8]}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = await upload.read()
            dest.write_bytes(content)

        # Parse all files
        nodes, edges = extract_from_directory(str(temp_dir))

        # Build graph and sweep
        graph = CodebaseGraph()
        graph.build_from_nodes_edges(nodes, edges)

        sweep = NFETSweep(alpha=1.0, beta=2.0, sigma_star=0.30, kappa_star=0.45, kappa_max=1.0)
        result = sweep.run(graph)

        phase_labels = {
            Phase.RIDGE: "Excellent",
            Phase.CAUTION: "Watch this",
            Phase.CRITICAL: "Needs attention",
        }

        top_components = []
        for nid, stress_val in result.top_stress_components[:5]:
            node_data = graph._graph.nodes.get(nid, {})
            top_components.append(ComponentHealth(
                name=node_data.get("name", nid),
                file_path=node_data.get("file_path", ""),
                stress=round(stress_val, 4),
                coupling=round(graph.coupling_score(nid), 4),
                cohesion=round(graph.cohesion_score(node_data.get("file_path", "")), 4),
                cascade_depth=graph.cascade_depth(nid),
            ))

        es = result.es_score
        if es >= 0.7:
            summary = f"Codebase is structurally healthy. {result.total_nodes} components analyzed, all within safe margins."
        elif es >= 0.4:
            summary = f"Codebase has structural concerns. {result.highest_stress_component} is carrying high stress."
        else:
            summary = f"Codebase has critical structural issues. Immediate refactoring recommended."

        recommendations = []
        if result.kappa > 0.6:
            recommendations.append("High coupling — extract shared logic into utility modules.")
        if result.sigma < 0.3:
            recommendations.append("Low stability margin — refactor highest-stress components.")
        if not recommendations:
            recommendations.append("No structural issues. Safe to add features.")

        return AnalyzeCodeResponse(
            report=HealthReport(
                phase=phase_labels.get(result.phase, "Unknown"),
                health_score=round(result.es_score, 4),
                coherence=round(result.kappa, 4),
                stability=round(result.sigma, 4),
                total_nodes=result.total_nodes,
                total_edges=result.total_edges,
                mean_coupling=round(result.mean_coupling, 4),
                mean_cohesion=round(result.mean_cohesion, 4),
                highest_stress_component=result.highest_stress_component,
                highest_stress_value=round(result.highest_stress_value, 4),
                top_components=top_components,
                summary=summary,
            ),
            recommendations=recommendations,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)[:200]}",
        )
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
