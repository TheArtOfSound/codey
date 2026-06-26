from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.nfet.controller import NFETController
from codey.nfet.repository_loader import build_graph_from_clone_url
from codey.saas.billing.plans import PLANS
from codey.saas.auth.dependencies import get_current_user
from codey.saas.database import get_db
from codey.saas.models import Repository, User

router = APIRouter(prefix="/repos", tags=["repos"])

_GITHUB_API_TIMEOUT = 20.0
_GITHUB_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_GITHUB_REPO_RE = re.compile(r"[A-Za-z0-9._-]+")
_REPO_URL_CREDENTIALS_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_REPO_QUERY_SECRET_RE = re.compile(
    r"([?&#](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token)=)[^&#\s]+",
    re.IGNORECASE,
)
_REPO_NAMED_SECRET_RE = re.compile(
    r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token|authorization)\b(\s*[:=]\s*)"
    r"(?:Bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
_REPO_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_ALLOWED_REPO_CLONE_SCHEMES = {"git", "git+ssh", "http", "https", "ssh"}
_ALLOWED_REPO_SCP_CLONE_HOSTS = {"github.com", "www.github.com"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ConnectRepoRequest(BaseModel):
    github_repo_url: str

    @field_validator("github_repo_url")
    @classmethod
    def _strip_and_validate_github_repo_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


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


class NFETComponentResponse(BaseModel):
    node_id: str
    name: str
    file_path: str
    kind: str
    stress: float
    coupling: float
    cohesion: float
    complexity: float
    cascade_depth: int
    impact_radius: int
    fanin: int
    fanout: int
    betweenness: float
    shared_state_edges: int
    cycle_detected: bool
    sigma: float
    kappa: float
    gamma: float
    es: float
    risk_score: float
    risk_level: str
    reasons: list[str]


class NFETCandidateResponse(BaseModel):
    candidate_id: str
    kind: str
    title: str
    description: str
    target_node_id: str
    target_file_path: str
    predicted_repo_es_delta: float
    predicted_sigma_reduction: float
    predicted_kappa_reduction: float
    risk: float
    cost: float
    reversibility: float
    confidence: float
    score: float
    reasons: list[str]


class RepoNFETSummaryResponse(BaseModel):
    repo_id: str
    full_name: str | None
    phase: str
    global_kappa: float
    global_sigma: float
    global_es: float
    total_nodes: int
    total_edges: int
    highest_stress_component: str
    highest_stress_value: float
    hotspot_count: int
    top_hotspots: list[NFETComponentResponse]


class RepoNFETHotspotsResponse(BaseModel):
    repo_id: str
    hotspots: list[NFETComponentResponse]


class RepoNFETCandidatesRequest(BaseModel):
    goal: str | None = None
    target_file: str | None = None
    limit: int = 5

    @field_validator("goal", "target_file")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("limit")
    @classmethod
    def _validate_positive_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be a positive integer")
        return value


class RepoNFETCandidatesResponse(BaseModel):
    repo_id: str
    phase: str
    global_es: float
    candidates: list[NFETCandidateResponse]


class RepoNFETSimulationRequest(BaseModel):
    candidate_id: str | None = None
    kind: str | None = None
    target_component: str | None = None
    target_file_path: str | None = None
    goal: str | None = None
    target_file: str | None = None

    @field_validator(
        "candidate_id",
        "kind",
        "target_component",
        "target_file_path",
        "goal",
        "target_file",
    )
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _require_candidate_selector(self) -> RepoNFETSimulationRequest:
        if not self.candidate_id and not self.kind:
            raise ValueError("candidate_id or kind is required")
        return self


class RepoNFETSimulationResponse(BaseModel):
    repo_id: str
    before_component_es: float
    after_component_es: float
    before_repo_es: float
    after_repo_es: float
    predicted_phase: str
    narrative: str
    candidate: NFETCandidateResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalize_repo_plan_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value or None


def _serialize_repo_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _coerce_non_empty_repo_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_repo_clone_url(value: Any) -> str | None:
    clone_url = _coerce_non_empty_repo_text(value)
    if (
        clone_url is None
        or _has_ascii_control(clone_url)
        or _has_whitespace(clone_url)
    ):
        return None
    return clone_url


def _has_unsafe_repo_clone_url_shape(clone_url: str) -> bool:
    if "?" in clone_url or "#" in clone_url:
        return True
    if "://" not in clone_url:
        user_host, separator, path = clone_url.partition(":")
        user, _, host = user_host.partition("@")
        return (
            separator != ":"
            or not path
            or user.lower() != "git"
            or host.lower() not in _ALLOWED_REPO_SCP_CLONE_HOSTS
        )
    try:
        parsed = urlparse(clone_url)
        port = parsed.port
    except ValueError:
        return True
    if parsed.scheme.lower() not in _ALLOWED_REPO_CLONE_SCHEMES:
        return True
    if port is not None and not (1 <= port <= 65535):
        return True
    if not parsed.hostname:
        return True
    if parsed.password is not None:
        return True
    if parsed.username is None:
        return False
    return (
        parsed.scheme.lower() not in {"git+ssh", "ssh"}
        or parsed.username.lower() != "git"
    )


def _coerce_repo_response_clone_url(value: Any) -> str | None:
    clone_url = _coerce_repo_clone_url(value)
    if clone_url is None or _has_unsafe_repo_clone_url_shape(clone_url):
        return None
    return clone_url


def _redact_repo_error(value: object) -> str:
    text = str(value)
    text = _REPO_URL_CREDENTIALS_RE.sub(r"\1***@", text)
    text = _REPO_QUERY_SECRET_RE.sub(r"\1***", text)

    def _replace_named_secret(match: re.Match[str]) -> str:
        prefix = f"{match.group(1)}{match.group(2)}"
        if "bearer" in match.group(0).lower():
            return f"{prefix}Bearer ***"
        return f"{prefix}***"

    text = _REPO_NAMED_SECRET_RE.sub(_replace_named_secret, text)
    return _REPO_EMAIL_RE.sub("[redacted-email]", text)


def _current_user_github_token(current_user: Any) -> str | None:
    return _coerce_github_bearer_token(getattr(current_user, "github_token", None))


def _coerce_github_bearer_token(value: Any) -> str | None:
    token = _coerce_non_empty_repo_text(value)
    if token is None or _has_ascii_control(token) or _has_whitespace(token):
        return None
    return token


def _coerce_repo_float(value: Any, fallback: float = 0.0) -> float:
    normalized: float
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        normalized = float(value)
    elif isinstance(value, str):
        try:
            normalized = float(value.strip())
        except ValueError:
            return fallback
    else:
        return fallback
    return normalized if math.isfinite(normalized) else fallback


def _coerce_repo_int(value: Any, fallback: int = 0) -> int:
    normalized: float
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        normalized = value
    elif isinstance(value, str):
        try:
            normalized = float(value.strip())
        except ValueError:
            return fallback
    else:
        return fallback
    return int(normalized) if math.isfinite(normalized) else fallback


def _repo_scalar_value(result: Any) -> Any:
    if hasattr(result, "scalar_one"):
        return result.scalar_one()
    if hasattr(result, "scalar_one_or_none"):
        return result.scalar_one_or_none()
    return None


def _coerce_optional_repo_int(value: Any) -> int | None:
    normalized: float
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        normalized = value
    elif isinstance(value, str):
        try:
            normalized = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return int(normalized) if math.isfinite(normalized) else None


def _max_connected_repos_for_user(user: User) -> int:
    plan = _normalize_repo_plan_name(getattr(user, "plan", None))
    if plan == "enterprise":
        return -1

    features = PLANS.get(plan or "", {}).get("features")
    if not isinstance(features, dict):
        return 0

    return _coerce_repo_int(features.get("github_repos"), 0)


def _repo_plan_display_name(user: User) -> str:
    plan_display_name = _coerce_non_empty_repo_text(getattr(user, "plan_display_name", None))
    if plan_display_name:
        return plan_display_name

    plan = _normalize_repo_plan_name(getattr(user, "plan", None))
    if plan == "enterprise":
        return "Enterprise"

    configured_name = PLANS.get(plan or "", {}).get("name")
    if isinstance(configured_name, str) and configured_name.strip():
        return configured_name.strip()

    return "Free"


def _coerce_repo_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        normalized = float(value)
        return bool(normalized) if math.isfinite(normalized) else fallback
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
    return fallback


def _coerce_repo_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        normalized = _coerce_non_empty_repo_text(item)
        if normalized is not None:
            items.append(normalized)
    return items


def _coerce_repo_row_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _repo_to_response(repo: Repository) -> RepoResponse:
    raw_autonomous_config = getattr(repo, "autonomous_config", None)
    autonomous_config = (
        raw_autonomous_config if isinstance(raw_autonomous_config, dict) else None
    )
    return RepoResponse(
        id=str(repo.id),
        full_name=_coerce_non_empty_repo_text(getattr(repo, "full_name", None)),
        clone_url=_coerce_repo_response_clone_url(getattr(repo, "clone_url", None)),
        default_branch=_coerce_non_empty_repo_text(
            getattr(repo, "default_branch", None)
        ),
        language=_coerce_non_empty_repo_text(getattr(repo, "language", None)),
        autonomous_mode_enabled=_coerce_repo_bool(
            getattr(repo, "autonomous_mode_enabled", None), False
        ),
        autonomous_config=autonomous_config,
        last_analyzed=_serialize_repo_timestamp(getattr(repo, "last_analyzed", None)),
        nfet_phase=_coerce_non_empty_repo_text(getattr(repo, "nfet_phase", None)),
        es_score=_coerce_repo_float(getattr(repo, "es_score", None), 0.0),
        created_at=_serialize_repo_timestamp(getattr(repo, "created_at", None)) or "",
    )


def _health_to_response(repo: Repository) -> RepoHealthResponse:
    return RepoHealthResponse(
        repo_id=str(repo.id),
        full_name=_coerce_non_empty_repo_text(getattr(repo, "full_name", None)),
        nfet_phase=_coerce_non_empty_repo_text(getattr(repo, "nfet_phase", None)),
        es_score=_coerce_repo_float(getattr(repo, "es_score", None), 0.0),
        last_analyzed=_serialize_repo_timestamp(getattr(repo, "last_analyzed", None)),
        autonomous_mode_enabled=_coerce_repo_bool(
            getattr(repo, "autonomous_mode_enabled", None), False
        ),
    )


def _summary_to_response(
    repo: Repository,
    repo_state: Any,
) -> RepoNFETSummaryResponse:
    raw_hotspots = getattr(repo_state, "hotspots", None)
    hotspots = raw_hotspots if isinstance(raw_hotspots, list) else []
    return RepoNFETSummaryResponse(
        repo_id=str(repo.id),
        full_name=_coerce_non_empty_repo_text(getattr(repo, "full_name", None)),
        phase=_coerce_non_empty_repo_text(getattr(repo_state, "phase", None)) or "",
        global_kappa=_coerce_repo_float(getattr(repo_state, "global_kappa", None), 0.0),
        global_sigma=_coerce_repo_float(getattr(repo_state, "global_sigma", None), 0.0),
        global_es=_coerce_repo_float(getattr(repo_state, "global_es", None), 0.0),
        total_nodes=_coerce_repo_int(getattr(repo_state, "total_nodes", None), 0),
        total_edges=_coerce_repo_int(getattr(repo_state, "total_edges", None), 0),
        highest_stress_component=(
            _coerce_non_empty_repo_text(getattr(repo_state, "highest_stress_component", None))
            or ""
        ),
        highest_stress_value=_coerce_repo_float(
            getattr(repo_state, "highest_stress_value", None),
            0.0,
        ),
        hotspot_count=len(hotspots),
        top_hotspots=[_component_to_response(h) for h in hotspots],
    )


def _candidates_to_response(
    repo: Repository,
    repo_state: Any,
    candidates: Any,
) -> RepoNFETCandidatesResponse:
    raw_candidates = candidates if isinstance(candidates, (list, tuple)) else []
    return RepoNFETCandidatesResponse(
        repo_id=str(repo.id),
        phase=_coerce_non_empty_repo_text(getattr(repo_state, "phase", None)) or "",
        global_es=_coerce_repo_float(getattr(repo_state, "global_es", None), 0.0),
        candidates=[_candidate_to_response(candidate) for candidate in raw_candidates],
    )


def _hotspots_to_response(
    repo: Repository,
    repo_state: Any,
) -> RepoNFETHotspotsResponse:
    raw_hotspots = getattr(repo_state, "hotspots", None)
    hotspots = raw_hotspots if isinstance(raw_hotspots, list) else []
    return RepoNFETHotspotsResponse(
        repo_id=str(repo.id),
        hotspots=[_component_to_response(h) for h in hotspots],
    )


def _simulation_to_response(
    repo: Repository,
    simulation: Any,
) -> RepoNFETSimulationResponse:
    return RepoNFETSimulationResponse(
        repo_id=str(repo.id),
        before_component_es=_coerce_repo_float(
            getattr(simulation, "before_component_es", None),
            0.0,
        ),
        after_component_es=_coerce_repo_float(
            getattr(simulation, "after_component_es", None),
            0.0,
        ),
        before_repo_es=_coerce_repo_float(getattr(simulation, "before_repo_es", None), 0.0),
        after_repo_es=_coerce_repo_float(getattr(simulation, "after_repo_es", None), 0.0),
        predicted_phase=_coerce_non_empty_repo_text(getattr(simulation, "predicted_phase", None))
        or "",
        narrative=_coerce_non_empty_repo_text(getattr(simulation, "narrative", None)) or "",
        candidate=_candidate_to_response(getattr(simulation, "candidate", None)),
    )


async def _load_owned_repo(
    repo_id: str,
    current_user: User,
    db: AsyncSession,
) -> Repository:
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
    return repo


def _component_to_response(component: Any) -> NFETComponentResponse:
    to_dict = getattr(component, "to_dict", None)
    payload = to_dict() if callable(to_dict) else {}
    if not isinstance(payload, dict):
        payload = {}

    return NFETComponentResponse(
        node_id=_coerce_non_empty_repo_text(payload.get("node_id")) or "",
        name=_coerce_non_empty_repo_text(payload.get("name")) or "",
        file_path=_coerce_non_empty_repo_text(payload.get("file_path")) or "",
        kind=_coerce_non_empty_repo_text(payload.get("kind")) or "",
        stress=_coerce_repo_float(payload.get("stress"), 0.0),
        coupling=_coerce_repo_float(payload.get("coupling"), 0.0),
        cohesion=_coerce_repo_float(payload.get("cohesion"), 0.0),
        complexity=_coerce_repo_float(payload.get("complexity"), 0.0),
        cascade_depth=_coerce_repo_int(payload.get("cascade_depth"), 0),
        impact_radius=_coerce_repo_int(payload.get("impact_radius"), 0),
        fanin=_coerce_repo_int(payload.get("fanin"), 0),
        fanout=_coerce_repo_int(payload.get("fanout"), 0),
        betweenness=_coerce_repo_float(payload.get("betweenness"), 0.0),
        shared_state_edges=_coerce_repo_int(payload.get("shared_state_edges"), 0),
        cycle_detected=_coerce_repo_bool(payload.get("cycle_detected"), False),
        sigma=_coerce_repo_float(payload.get("sigma"), 0.0),
        kappa=_coerce_repo_float(payload.get("kappa"), 0.0),
        gamma=_coerce_repo_float(payload.get("gamma"), 0.0),
        es=_coerce_repo_float(payload.get("es"), 0.0),
        risk_score=_coerce_repo_float(payload.get("risk_score"), 0.0),
        risk_level=_coerce_non_empty_repo_text(payload.get("risk_level")) or "",
        reasons=_coerce_repo_string_list(payload.get("reasons")),
    )


def _candidate_to_response(candidate: Any) -> NFETCandidateResponse:
    to_dict = getattr(candidate, "to_dict", None)
    payload = to_dict() if callable(to_dict) else {}
    if not isinstance(payload, dict):
        payload = {}

    return NFETCandidateResponse(
        candidate_id=_coerce_non_empty_repo_text(payload.get("candidate_id")) or "",
        kind=_coerce_non_empty_repo_text(payload.get("kind")) or "",
        title=_coerce_non_empty_repo_text(payload.get("title")) or "",
        description=_coerce_non_empty_repo_text(payload.get("description")) or "",
        target_node_id=_coerce_non_empty_repo_text(payload.get("target_node_id")) or "",
        target_file_path=_coerce_non_empty_repo_text(payload.get("target_file_path")) or "",
        predicted_repo_es_delta=_coerce_repo_float(payload.get("predicted_repo_es_delta"), 0.0),
        predicted_sigma_reduction=_coerce_repo_float(payload.get("predicted_sigma_reduction"), 0.0),
        predicted_kappa_reduction=_coerce_repo_float(payload.get("predicted_kappa_reduction"), 0.0),
        risk=_coerce_repo_float(payload.get("risk"), 0.0),
        cost=_coerce_repo_float(payload.get("cost"), 0.0),
        reversibility=_coerce_repo_float(payload.get("reversibility"), 0.0),
        confidence=_coerce_repo_float(payload.get("confidence"), 0.0),
        score=_coerce_repo_float(payload.get("score"), 0.0),
        reasons=_coerce_repo_string_list(payload.get("reasons")),
    )


async def _analyze_repo_nfet(
    repo: Repository,
    token: str | None,
    *,
    goal: str | None = None,
    target_file: str | None = None,
) -> tuple[Any, Any, Any]:
    goal = _normalize_optional_text(goal)
    target_file = _normalize_optional_text(target_file)
    clone_url = _coerce_repo_clone_url(getattr(repo, "clone_url", None))

    if not clone_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository has no clone URL configured",
        )
    try:
        clone_full_name = _parse_github_url(clone_url)
    except HTTPException as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository has invalid clone URL configured",
        ) from exc
    repo_full_name = _coerce_non_empty_repo_text(getattr(repo, "full_name", None))
    if repo_full_name:
        try:
            canonical_full_name = _parse_github_url(repo_full_name)
        except HTTPException:
            canonical_full_name = None
        if (
            canonical_full_name
            and clone_full_name.lower() != canonical_full_name.lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Repository clone URL does not match repository",
            )

    try:
        graph = await build_graph_from_clone_url(clone_url, token=token)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Repository analysis failed: {_redact_repo_error(exc)[:200]}",
        )

    controller = NFETController()
    repo_state = controller.analyze(graph, goal=goal, target_file=target_file)
    return graph, controller, repo_state


def _parse_github_url(url: str) -> str:
    """Extract 'owner/repo' from a GitHub URL or pass-through if already in that form."""
    if not isinstance(url, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
        )

    url = url.strip().rstrip("/")
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
        )
    lower_url = url.lower()
    allowed_hosts = {"github.com", "www.github.com"}
    if lower_url.startswith("git@"):
        user_host, separator, path = url.partition(":")
        host = user_host.partition("@")[2].lower()
        if separator != ":" or host not in allowed_hosts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
            )
    elif lower_url.startswith("https://") or lower_url.startswith("ssh://"):
        parsed = urlparse(url)
        try:
            port = parsed.port
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
            )
        if port is not None and port <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
            )
        if parsed.scheme in {"http", "https"} and (
            parsed.username is not None or parsed.password is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
            )
        if parsed.scheme == "ssh" and parsed.password is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
            )
        if parsed.scheme == "ssh" and (parsed.username or "").lower() != "git":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
            )
        if (parsed.hostname or "").lower() not in allowed_hosts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
            )
        path = parsed.path.lstrip("/")
    elif lower_url.startswith("http://"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
        )
    elif "/" in url and not lower_url.startswith("http"):
        path = url.split("?", 1)[0].split("#", 1)[0]  # already "owner/repo"
        if ":" in path or path.count("/") != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
        )

    # Remove .git suffix and ignore any trailing path segments.
    path = path.removesuffix(".git")
    parts = path.split("/")
    if len(parts) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not parse owner/repo from URL",
        )
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if (
        not _GITHUB_OWNER_RE.fullmatch(owner)
        or not _GITHUB_REPO_RE.fullmatch(repo)
        or repo in {".", ".."}
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub repository URL. Expected https://github.com/owner/repo",
        )
    return f"{owner}/{repo}"


def _validate_github_clone_url(clone_url: str, canonical_full_name: str) -> None:
    try:
        clone_full_name = _parse_github_url(clone_url)
    except HTTPException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub repository metadata has an invalid clone_url.",
        ) from exc
    parsed = urlparse(clone_url)
    if parsed.scheme.lower() != "https" or parsed.query or parsed.fragment:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub repository metadata has an invalid clone_url.",
        )
    if clone_full_name.lower() != canonical_full_name.lower():
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub repository metadata clone_url does not match repository.",
        )


def _coerce_canonical_repo_full_name(value: Any, fallback: str) -> str:
    full_name = _coerce_non_empty_repo_text(value)
    if full_name:
        try:
            return _parse_github_url(full_name)
        except HTTPException:
            pass
    return fallback


async def _fetch_github_repo_info(full_name: str, token: str | None) -> dict:
    """Fetch repo metadata from the GitHub API."""
    full_name = _coerce_non_empty_repo_text(full_name)
    if not full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub repository name.",
        )
    try:
        full_name = _parse_github_url(full_name)
    except HTTPException as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub repository name.",
        ) from exc

    token = _coerce_github_bearer_token(token)
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=_GITHUB_API_TIMEOUT) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{full_name}",
                headers=headers,
            )
            if resp.status_code == 404:
                detail = f"GitHub repository '{full_name}' not found."
                if token:
                    detail += (
                        " If this repo is private or org-restricted, reconnect GitHub "
                        "from Settings to refresh repository access."
                    )
                else:
                    detail += (
                        " If this repo is private, connect GitHub from Settings before "
                        "adding it."
                    )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=detail,
                )
            if resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "GitHub denied repository access. Reconnect GitHub from "
                        "Settings and grant repository permissions, then try again."
                    ),
                )
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub API returned an invalid repository response. Try again.",
                ) from exc
            if not isinstance(data, dict):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub API returned an invalid repository response. Try again.",
                )
            return data
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"GitHub API timed out after {_GITHUB_API_TIMEOUT:.0f}s",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub API request failed. Try again.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub API request failed. Try again.",
        ) from exc


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
    repos = _coerce_repo_row_list(result.scalars().all())
    return [_repo_to_response(r) for r in repos]


@router.post("", response_model=RepoResponse, status_code=status.HTTP_201_CREATED)
async def connect_repo(
    body: ConnectRepoRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepoResponse:
    full_name = _parse_github_url(body.github_repo_url)

    # Fetch repo info from GitHub
    github_token = _current_user_github_token(current_user)
    gh_data = await _fetch_github_repo_info(full_name, github_token)
    canonical_full_name = _coerce_canonical_repo_full_name(
        gh_data.get("full_name"),
        full_name,
    )
    clone_url = _coerce_repo_clone_url(gh_data.get("clone_url"))
    default_branch = _coerce_non_empty_repo_text(gh_data.get("default_branch")) or "main"
    language = _coerce_non_empty_repo_text(gh_data.get("language"))
    if clone_url is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub repository metadata is missing clone_url.",
        )
    _validate_github_clone_url(clone_url, canonical_full_name)

    # Check for duplicate using GitHub's canonical name so mixed-case URLs do
    # not create duplicate connections for the same repository.
    existing_stmt = select(Repository).where(
        Repository.user_id == current_user.id,
        func.lower(Repository.full_name) == canonical_full_name.lower(),
    )
    existing_result = await db.execute(existing_stmt)
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Repository '{canonical_full_name}' is already connected",
        )

    repo_limit = _max_connected_repos_for_user(current_user)
    if repo_limit >= 0:
        count_stmt = select(func.count(Repository.id)).where(
            Repository.user_id == current_user.id
        )
        count_result = await db.execute(count_stmt)
        connected_repo_count = max(
            0,
            _coerce_repo_int(_repo_scalar_value(count_result), 0) or 0,
        )
        if connected_repo_count >= repo_limit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"The {_repo_plan_display_name(current_user)} plan allows up to "
                    f"{repo_limit} connected GitHub repo(s)."
                ),
            )

    repo = Repository(
        user_id=current_user.id,
        github_repo_id=_coerce_optional_repo_int(gh_data.get("id")),
        full_name=canonical_full_name,
        clone_url=clone_url,
        default_branch=default_branch,
        language=language,
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
    repo = await _load_owned_repo(repo_id, current_user, db)
    await db.delete(repo)
    await db.flush()


@router.patch("/{repo_id}/autonomous", response_model=RepoResponse)
async def toggle_autonomous_mode(
    repo_id: str,
    body: AutonomousModeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepoResponse:
    repo = await _load_owned_repo(repo_id, current_user, db)

    # Autonomous mode is available on all plans (each queued run is credit-metered).
    clone_url = _coerce_repo_clone_url(getattr(repo, "clone_url", None))
    if body.enabled and not clone_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository has no clone URL configured",
        )
    if body.enabled and clone_url:
        if _has_unsafe_repo_clone_url_shape(clone_url):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Repository has invalid clone URL configured",
            )
        try:
            clone_full_name = _parse_github_url(clone_url)
        except HTTPException as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Repository has invalid clone URL configured",
            ) from exc
        repo_full_name = _coerce_non_empty_repo_text(getattr(repo, "full_name", None))
        if repo_full_name:
            try:
                canonical_full_name = _parse_github_url(repo_full_name)
            except HTTPException:
                canonical_full_name = None
            if (
                canonical_full_name
                and clone_full_name.lower() != canonical_full_name.lower()
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Repository clone URL does not match repository",
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
    repo = await _load_owned_repo(repo_id, current_user, db)
    return _health_to_response(repo)


@router.get("/{repo_id}/activity", response_model=RepoActivityResponse)
async def get_repo_activity(
    repo_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepoActivityResponse:
    repo = await _load_owned_repo(repo_id, current_user, db)

    # Pull autonomous action log from the repo's autonomous_config
    # In production this would come from a dedicated activity log table
    raw_config = getattr(repo, "autonomous_config", None)
    config = raw_config if isinstance(raw_config, dict) else {}
    raw_log: list[dict] = config.get("activity_log", [])

    entries: list[ActivityEntry] = []
    if isinstance(raw_log, list):
        for entry in raw_log:
            if not isinstance(entry, dict):
                continue
            action = entry.get("action", "unknown")
            if not isinstance(action, str) or not action.strip():
                action = "unknown"
            else:
                action = action.strip()

            timestamp = entry.get("timestamp", "")
            if not isinstance(timestamp, str):
                timestamp = str(timestamp or "")

            details = entry.get("details")
            if details is not None and not isinstance(details, dict):
                details = None

            entries.append(
                ActivityEntry(
                    action=action,
                    timestamp=timestamp,
                    details=details,
                )
            )

    return RepoActivityResponse(
        repo_id=str(repo.id),
        entries=entries,
    )


@router.get("/{repo_id}/nfet/summary", response_model=RepoNFETSummaryResponse)
async def get_repo_nfet_summary(
    repo_id: str,
    goal: str | None = None,
    target_file: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepoNFETSummaryResponse:
    repo = await _load_owned_repo(repo_id, current_user, db)
    _, _, repo_state = await _analyze_repo_nfet(
        repo,
        _current_user_github_token(current_user),
        goal=goal,
        target_file=target_file,
    )
    return _summary_to_response(repo, repo_state)


@router.get("/{repo_id}/nfet/hotspots", response_model=RepoNFETHotspotsResponse)
async def get_repo_nfet_hotspots(
    repo_id: str,
    goal: str | None = None,
    target_file: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepoNFETHotspotsResponse:
    repo = await _load_owned_repo(repo_id, current_user, db)
    _, _, repo_state = await _analyze_repo_nfet(
        repo,
        _current_user_github_token(current_user),
        goal=goal,
        target_file=target_file,
    )
    return _hotspots_to_response(repo, repo_state)


@router.post("/{repo_id}/nfet/candidates", response_model=RepoNFETCandidatesResponse)
async def get_repo_nfet_candidates(
    repo_id: str,
    body: RepoNFETCandidatesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepoNFETCandidatesResponse:
    repo = await _load_owned_repo(repo_id, current_user, db)
    graph, controller, repo_state = await _analyze_repo_nfet(
        repo,
        _current_user_github_token(current_user),
        goal=body.goal,
        target_file=body.target_file,
    )
    candidates = controller.rank_interventions(
        graph,
        goal=body.goal,
        target_file=body.target_file,
        limit=max(1, min(body.limit, 10)),
        repo_state=repo_state,
    )
    return _candidates_to_response(repo, repo_state, candidates)


@router.post("/{repo_id}/nfet/simulate", response_model=RepoNFETSimulationResponse)
async def simulate_repo_nfet_candidate(
    repo_id: str,
    body: RepoNFETSimulationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepoNFETSimulationResponse:
    repo = await _load_owned_repo(repo_id, current_user, db)
    graph, controller, repo_state = await _analyze_repo_nfet(
        repo,
        _current_user_github_token(current_user),
        goal=body.goal,
        target_file=body.target_file,
    )
    candidates = controller.rank_interventions(
        graph,
        goal=body.goal,
        target_file=body.target_file,
        limit=10,
        repo_state=repo_state,
        focus_component=body.target_component or body.target_file_path,
    )

    selected = None
    if body.candidate_id:
        for candidate in candidates:
            if candidate.candidate_id == body.candidate_id:
                selected = candidate
                break
    elif body.kind:
        for candidate in candidates:
            if candidate.kind == body.kind:
                selected = candidate
                break

    if selected is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No matching NFET candidate found for simulation",
        )

    simulation = controller.simulate_action(
        graph,
        selected,
        goal=body.goal,
        target_file=body.target_file,
        repo_state=repo_state,
    )
    return _simulation_to_response(repo, simulation)
