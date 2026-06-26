"""Structural health analysis — wires parser + graph engine + NFET sweep into API endpoints."""
from __future__ import annotations

import tempfile
import uuid
import re
import math
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.database import get_db
from codey.saas.models import User

router = APIRouter(tags=["health"])

_MAX_HEALTH_METRIC = 1_000_000.0
_MAX_ANALYZE_UPLOAD_FILES = 100
_MAX_ANALYZE_UPLOAD_BYTES = 5 * 1024 * 1024
_MAX_ANALYZE_UPLOAD_TOTAL_BYTES = 20 * 1024 * 1024
_MAX_ANALYZE_UPLOAD_PATH_PART_CHARS = 180
_MAX_ANALYZE_UPLOAD_PATH_CHARS = 512
_HEALTH_URL_CREDENTIAL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_HEALTH_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|password|secret|token)=)[^&#\s]+"
)
_HEALTH_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|password|secret|token|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_HEALTH_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AnalyzeCodeRequest(BaseModel):
    code: str
    filename: str = "main.py"
    language: str = "python"

    @field_validator("code")
    @classmethod
    def _validate_non_blank_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


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
# Helpers
# ---------------------------------------------------------------------------


def _coerce_health_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                items.append(normalized)
    return items


def _coerce_health_text(value: Any, default: str) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or default
    if value is None:
        return default
    normalized = str(value).strip()
    return normalized or default


def _coerce_health_metric(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        metric = float(value)
    except OverflowError:
        metric = _MAX_HEALTH_METRIC
    except (TypeError, ValueError):
        metric = 0.0
    if not math.isfinite(metric):
        metric = _MAX_HEALTH_METRIC if metric > 0 else 0.0
    elif metric < 0.0:
        metric = 0.0
    else:
        metric = min(metric, _MAX_HEALTH_METRIC)
    return metric


def _round_health_metric(value: Any, ndigits: int = 4) -> float:
    return round(_coerce_health_metric(value), ndigits)


def _coerce_top_stress_components(
    value: Any, limit: int
) -> list[tuple[str, float]]:
    if not isinstance(value, (list, tuple)):
        return []

    components: list[tuple[str, float]] = []
    for item in value:
        if isinstance(item, dict):
            node_id = item.get("id") or item.get("component") or item.get("name")
            stress_value = item.get("stress")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            node_id = item[0]
            stress_value = item[1]
        else:
            continue

        if not isinstance(node_id, str) or not node_id:
            continue

        components.append((node_id, _coerce_health_metric(stress_value)))
        if len(components) >= limit:
            break

    return components


def _redact_health_error(value: object) -> str:
    text = str(value)
    text = _HEALTH_URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _HEALTH_URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _HEALTH_NAMED_SECRET_RE.sub(r"\1***", text)
    return _HEALTH_EMAIL_ADDRESS_RE.sub(r"***@\1", text)


# ---------------------------------------------------------------------------
# Upload path helpers
# ---------------------------------------------------------------------------


def _safe_upload_destination(temp_dir: Path, filename: str | None) -> Path:
    """Map an uploaded filename into a safe path under ``temp_dir``."""
    if not filename:
        return temp_dir / f"file_{uuid.uuid4().hex[:8]}"

    normalized = filename.replace("\\", "/")
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid upload filename",
        )
    path_obj = PurePosixPath(normalized)
    if path_obj.is_absolute() or any(part.endswith(":") for part in path_obj.parts):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid upload filename",
        )

    parts = [
        part
        for part in path_obj.parts
        if part not in {"", ".", "/"}
    ]
    if (
        not parts
        or any(part == ".." for part in parts)
        or any(len(part) > _MAX_ANALYZE_UPLOAD_PATH_PART_CHARS for part in parts)
        or len("/".join(parts)) > _MAX_ANALYZE_UPLOAD_PATH_CHARS
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid upload filename",
        )

    dest = (temp_dir / Path(*parts)).resolve()
    temp_root = temp_dir.resolve()
    if temp_root not in {dest, *dest.parents}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid upload filename",
        )
    return dest


def _dedupe_upload_destination(destination: Path, seen: set[Path]) -> Path:
    """Return a unique upload destination without overwriting earlier files."""
    resolved = destination.resolve(strict=False)
    if resolved not in seen:
        seen.add(resolved)
        return destination

    parent = destination.parent
    suffix = destination.suffix
    stem = destination.name[: -len(suffix)] if suffix else destination.name
    stem = stem or "file"
    index = 2
    while True:
        marker = f"-{index}"
        max_stem_chars = max(
            1,
            _MAX_ANALYZE_UPLOAD_PATH_PART_CHARS - len(marker) - len(suffix),
        )
        candidate = parent / f"{stem[:max_stem_chars]}{marker}{suffix}"
        resolved_candidate = candidate.resolve(strict=False)
        if resolved_candidate not in seen:
            seen.add(resolved_candidate)
            return candidate
        index += 1


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
    top_stress_components = _coerce_top_stress_components(
        result.top_stress_components, limit=5
    )
    for nid, stress_val in top_stress_components:
        node_data = graph._graph.nodes.get(nid, {})
        component_path = _coerce_health_text(node_data.get("file_path"), filename)
        top_components.append({
            "name": _coerce_health_text(node_data.get("name"), nid),
            "file_path": component_path,
            "stress": _round_health_metric(stress_val),
            "coupling": _round_health_metric(graph.coupling_score(nid)),
            "cohesion": _round_health_metric(
                graph.cohesion_score(component_path)
            ),
            "cascade_depth": graph.cascade_depth(nid),
        })

    # Generate plain language summary
    es = _coerce_health_metric(result.es_score)
    highest_stress_component = _coerce_health_text(
        result.highest_stress_component,
        "unknown",
    )
    if es >= 0.7:
        summary = (
            f"Your code is structurally healthy. "
            f"{result.total_nodes} components with safe stability margins. "
            f"No cascade risks detected."
        )
    elif es >= 0.4:
        summary = (
            f"Your code has some structural concerns. "
            f"Component '{highest_stress_component}' is carrying "
            f"high coupling relative to its cohesion. "
            f"Consider refactoring before adding more dependencies."
        )
    else:
        summary = (
            f"Your code has critical structural issues. "
            f"Component '{highest_stress_component}' has crossed "
            f"the cascade threshold — a failure here could propagate widely. "
            f"Immediate refactoring recommended."
        )

    # Generate recommendations
    recommendations = []
    if _coerce_health_metric(result.kappa) > 0.6:
        recommendations.append(
            "High coupling density. Consider extracting shared logic into utility modules."
        )
    if _coerce_health_metric(result.sigma) < 0.3:
        recommendations.append(
            "Low stability margin. The highest-stress component is close to cascade risk."
        )
    if _coerce_health_metric(result.mean_cohesion) < 0.4:
        recommendations.append(
            "Low module cohesion. Components have too many external dependencies relative to internal ones."
        )
    for nid, safe_stress in _coerce_top_stress_components(
        result.top_stress_components, limit=3
    ):
        if safe_stress > 0.7:
            name = _coerce_health_text(
                graph._graph.nodes.get(nid, {}).get("name"),
                nid,
            )
            recommendations.append(
                f"Refactor '{name}' — stress score {safe_stress:.2f} indicates fragility."
            )
    if not recommendations:
        recommendations.append("No structural issues detected. Safe to add features.")

    return {
        "phase": phase_labels.get(result.phase, "Unknown"),
        "health_score": _round_health_metric(result.es_score),
        "coherence": _round_health_metric(result.kappa),
        "stability": _round_health_metric(result.sigma),
        "total_nodes": result.total_nodes,
        "total_edges": result.total_edges,
        "mean_coupling": _round_health_metric(result.mean_coupling),
        "mean_cohesion": _round_health_metric(result.mean_cohesion),
        "highest_stress_component": highest_stress_component,
        "highest_stress_value": _round_health_metric(result.highest_stress_value),
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
        recommendations = _coerce_health_string_list(
            result.get("recommendations") if isinstance(result, dict) else None
        )
        return AnalyzeCodeResponse(
            report=HealthReport(**{k: v for k, v in result.items() if k != "recommendations"}),
            recommendations=recommendations,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {_redact_health_error(e)[:200]}",
        )


@router.post("/analyze/upload", response_model=AnalyzeCodeResponse)
async def analyze_upload(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
) -> AnalyzeCodeResponse:
    """Upload files and get a structural health report."""
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files uploaded",
        )
    if len(files) > _MAX_ANALYZE_UPLOAD_FILES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Too many uploaded files",
        )

    from codey.parser.extractor import extract_from_directory
    from codey.graph.engine import CodebaseGraph
    from codey.nfet.sweep import NFETSweep, Phase

    # Save uploaded files to temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="codey_analyze_"))
    try:
        total_upload_bytes = 0
        seen_upload_destinations: set[Path] = set()
        for upload in files:
            dest = _dedupe_upload_destination(
                _safe_upload_destination(temp_dir, upload.filename),
                seen_upload_destinations,
            )
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = await upload.read(_MAX_ANALYZE_UPLOAD_BYTES + 1)
            if len(content) > _MAX_ANALYZE_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Uploaded file is too large",
                )
            total_upload_bytes += len(content)
            if total_upload_bytes > _MAX_ANALYZE_UPLOAD_TOTAL_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Uploaded files are too large",
                )
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
        top_stress_components = _coerce_top_stress_components(
            result.top_stress_components, limit=5
        )
        for nid, stress_val in top_stress_components:
            node_data = graph._graph.nodes.get(nid, {})
            component_path = _coerce_health_text(node_data.get("file_path"), "")
            top_components.append(ComponentHealth(
                name=_coerce_health_text(node_data.get("name"), nid),
                file_path=component_path,
                stress=_round_health_metric(stress_val),
                coupling=_round_health_metric(graph.coupling_score(nid)),
                cohesion=_round_health_metric(
                    graph.cohesion_score(component_path)
                ),
                cascade_depth=graph.cascade_depth(nid),
            ))

        es = _coerce_health_metric(result.es_score)
        highest_stress_component = _coerce_health_text(
            result.highest_stress_component,
            "unknown",
        )
        if es >= 0.7:
            summary = f"Codebase is structurally healthy. {result.total_nodes} components analyzed, all within safe margins."
        elif es >= 0.4:
            summary = f"Codebase has structural concerns. {highest_stress_component} is carrying high stress."
        else:
            summary = f"Codebase has critical structural issues. Immediate refactoring recommended."

        recommendations = []
        if _coerce_health_metric(result.kappa) > 0.6:
            recommendations.append("High coupling — extract shared logic into utility modules.")
        if _coerce_health_metric(result.sigma) < 0.3:
            recommendations.append("Low stability margin — refactor highest-stress components.")
        if not recommendations:
            recommendations.append("No structural issues. Safe to add features.")

        return AnalyzeCodeResponse(
            report=HealthReport(
                phase=phase_labels.get(result.phase, "Unknown"),
                health_score=_round_health_metric(result.es_score),
                coherence=_round_health_metric(result.kappa),
                stability=_round_health_metric(result.sigma),
                total_nodes=result.total_nodes,
                total_edges=result.total_edges,
                mean_coupling=_round_health_metric(result.mean_coupling),
                mean_cohesion=_round_health_metric(result.mean_cohesion),
                highest_stress_component=highest_stress_component,
                highest_stress_value=_round_health_metric(result.highest_stress_value),
                top_components=top_components,
                summary=summary,
            ),
            recommendations=recommendations,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {_redact_health_error(e)[:200]}",
        )
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
