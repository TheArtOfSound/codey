from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from codey.saas.tasks.asyncio_utils import run_sync_task
from codey.saas.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_AUTONOMOUS_IMPROVEMENT_LIMIT = 5
_AUTONOMOUS_URL_CREDENTIALS_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_AUTONOMOUS_QUERY_SECRET_RE = re.compile(
    r"([?&#](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token)=)[^&#\s]+",
    re.IGNORECASE,
)
_AUTONOMOUS_NAMED_SECRET_RE = re.compile(
    r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token|authorization)\b(\s*[:=]\s*)"
    r"(?:Bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
_AUTONOMOUS_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_ALLOWED_AUTONOMOUS_CLONE_SCHEMES = {"git", "git+ssh", "http", "https", "ssh"}
_ALLOWED_AUTONOMOUS_SCP_CLONE_HOSTS = {"github.com", "www.github.com"}


def _coerce_autonomous_config(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _coerce_autonomous_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_autonomous_github_token(value: Any) -> str | None:
    token = _coerce_autonomous_text(value)
    if token is None or _has_ascii_control(token) or _has_whitespace(token):
        return None
    return token


def _coerce_autonomous_clone_url(value: Any) -> str | None:
    clone_url = _coerce_autonomous_text(value)
    if (
        clone_url is None
        or _has_ascii_control(clone_url)
        or _has_whitespace(clone_url)
    ):
        return None
    if "?" in clone_url or "#" in clone_url:
        return None
    if "://" not in clone_url:
        user_host, separator, path = clone_url.partition(":")
        user, _, host = user_host.partition("@")
        if (
            separator != ":"
            or not path
            or user.lower() != "git"
            or host.lower() not in _ALLOWED_AUTONOMOUS_SCP_CLONE_HOSTS
        ):
            return None
    else:
        try:
            split = urlsplit(clone_url)
            port = split.port
        except ValueError:
            return None
        scheme = split.scheme.lower()
        if scheme not in _ALLOWED_AUTONOMOUS_CLONE_SCHEMES:
            return None
        if port is not None and port <= 0:
            return None
        if split.hostname is None:
            return None
        if scheme in {"http", "https"} and (
            split.username is not None or split.password is not None
        ):
            return None
        if split.password is not None:
            return None
        if split.username is not None and (
            scheme not in {"git+ssh", "ssh"} or split.username.lower() != "git"
        ):
            return None
    return clone_url


def _coerce_autonomous_identifier(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    identifier = _coerce_autonomous_text(value)
    if (
        identifier is None
        or _has_ascii_control(identifier)
        or _has_whitespace(identifier)
    ):
        return None
    return identifier


def _redact_autonomous_error(value: object) -> str:
    text = _AUTONOMOUS_URL_CREDENTIALS_RE.sub(r"\1***@", str(value))
    text = _AUTONOMOUS_QUERY_SECRET_RE.sub(r"\1***", text)

    def _replace_named_secret(match: re.Match[str]) -> str:
        prefix = f"{match.group(1)}{match.group(2)}"
        if "bearer" in match.group(0).lower():
            return f"{prefix}Bearer ***"
        return f"{prefix}***"

    text = _AUTONOMOUS_NAMED_SECRET_RE.sub(_replace_named_secret, text)
    return _AUTONOMOUS_EMAIL_RE.sub("[redacted-email]", text)


def _coerce_autonomous_mapping_rows(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _coerce_autonomous_mapping_row(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _coerce_autonomous_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        return default
    if not math.isfinite(normalized):
        return default
    return normalized


def _coerce_autonomous_unit_float(value: Any, default: float = 0.0) -> float:
    normalized = _coerce_autonomous_float(value, default)
    if normalized < 0.0:
        return default
    return min(1.0, normalized)


def _coerce_autonomous_delta(value: Any, default: float = 0.0) -> float:
    normalized = _coerce_autonomous_float(value, default)
    return max(-1.0, min(1.0, normalized))


def _coerce_stress_threshold(value: Any, default: float = 0.7) -> float:
    normalized = _coerce_autonomous_float(value, default)
    if normalized < 0.0 or normalized > 1.0:
        return default
    return normalized


def _coerce_autonomous_dispatch_limit(
    value: Any,
    default: int = 100,
    minimum: int = 1,
    maximum: int = 1000,
) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


@celery_app.task(
    name="codey.saas.tasks.autonomous.run_autonomous_repo",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def run_autonomous_repo(self, repo_id: str, user_id: str) -> dict:
    """Run autonomous analysis and improvements on a single repository.

    Clones the repo, analyses the codebase via NFET, generates improvements,
    opens a PR if configured, and records results.
    """
    safe_repo_id = _coerce_autonomous_identifier(repo_id)
    safe_user_id = _coerce_autonomous_identifier(user_id)
    if safe_repo_id is None or safe_user_id is None:
        logger.warning("Skipping autonomous run with malformed identifiers")
        return {"status": "skipped", "repo_id": safe_repo_id or "unknown"}

    repo_id = safe_repo_id
    user_id = safe_user_id

    from codey.saas.database import async_session_factory

    async def _run() -> dict:
        async with async_session_factory() as db:
            from sqlalchemy import text

            # Fetch repo config
            row = await db.execute(
                text(
                    "SELECT r.id, r.full_name, r.clone_url, r.default_branch, "
                    "r.autonomous_config, u.github_token "
                    "FROM repositories r "
                    "JOIN users u ON u.id = r.user_id "
                    "WHERE r.id = :rid AND r.user_id = :uid "
                    "AND autonomous_mode_enabled = true"
                ),
                {"rid": repo_id, "uid": user_id},
            )
            repo = _coerce_autonomous_mapping_row(row.mappings().first())
            if repo is None:
                logger.warning(
                    "Repo %s not found or autonomous disabled",
                    _redact_autonomous_error(repo_id),
                )
                return {"status": "skipped", "repo_id": repo_id}

            repo_full_name = _coerce_autonomous_text(repo.get("full_name")) or repo_id
            clone_url = _coerce_autonomous_clone_url(repo.get("clone_url"))
            if not clone_url:
                logger.warning(
                    "Repo %s has no valid clone_url; skipping autonomous run",
                    _redact_autonomous_error(repo_id),
                )
                return {"status": "skipped", "repo_id": repo_id}

            logger.info(
                "Running autonomous analysis on %s (%s)",
                _redact_autonomous_error(repo_full_name),
                _redact_autonomous_error(repo_id),
            )

            from codey.saas.security.encryption import decrypt_token
            from codey.nfet.controller import NFETController
            from codey.nfet.repository_loader import build_graph_from_clone_url_sync
            from codey.nfet.sweep import NFETSweep

            config = _coerce_autonomous_config(repo.get("autonomous_config"))
            stress_threshold = _coerce_stress_threshold(config.get("stress_trigger"))
            token = _coerce_autonomous_github_token(repo.get("github_token"))
            if token:
                encrypted_token = token
                try:
                    token = _coerce_autonomous_github_token(decrypt_token(encrypted_token))
                except Exception:
                    # Legacy plaintext tokens should still work during transition.
                    token = encrypted_token

            # 1. Clone repo and build graph
            try:
                graph = build_graph_from_clone_url_sync(
                    clone_url,
                    token=token,
                )
            except ValueError as exc:
                logger.warning(
                    "Repo %s has an invalid clone_url; skipping autonomous run: %s",
                    _redact_autonomous_error(repo_id),
                    _redact_autonomous_error(exc),
                )
                return {"status": "skipped", "repo_id": repo_id}
            except RuntimeError as exc:
                logger.warning(
                    "Repo %s failed to clone during autonomous run: %s",
                    _redact_autonomous_error(repo_id),
                    _redact_autonomous_error(exc),
                )
                return {
                    "status": "failed",
                    "repo_id": repo_id,
                    "reason": "clone_failed",
                }
            except Exception as exc:
                logger.warning(
                    "Repo %s failed to load during autonomous run: %s",
                    _redact_autonomous_error(repo_id),
                    _redact_autonomous_error(exc),
                )
                return {
                    "status": "failed",
                    "repo_id": repo_id,
                    "reason": "repo_load_failed",
                }

            # 2. NFET analysis and controller ranking
            try:
                sweep = NFETSweep()
                result = sweep.run(graph)
                controller = NFETController(sweep_engine=sweep)
                repo_state = controller.analyze(
                    graph,
                    goal="scheduled autonomous maintenance",
                )
                candidates = controller.rank_interventions(
                    graph,
                    goal="scheduled autonomous maintenance",
                    repo_state=repo_state,
                    limit=_AUTONOMOUS_IMPROVEMENT_LIMIT,
                )
            except Exception as exc:
                logger.warning(
                    "Repo %s failed NFET analysis during autonomous run: %s",
                    _redact_autonomous_error(repo_id),
                    _redact_autonomous_error(exc),
                )
                return {
                    "status": "failed",
                    "repo_id": repo_id,
                    "reason": "analysis_failed",
                }
            raw_components = getattr(repo_state, "components", None)
            components = raw_components if isinstance(raw_components, list) else []
            raw_hotspots = getattr(repo_state, "hotspots", None)
            hotspots = raw_hotspots if isinstance(raw_hotspots, list) else []
            if not isinstance(candidates, list):
                candidates = []
            else:
                candidates = candidates[:_AUTONOMOUS_IMPROVEMENT_LIMIT]
            phase_value = (
                _coerce_autonomous_text(getattr(getattr(result, "phase", None), "value", None))
                or "unknown"
            )
            es_score = _coerce_autonomous_unit_float(
                getattr(result, "es_score", None),
                0.0,
            )

            improvements = []
            for candidate in candidates:
                target_node_id = getattr(candidate, "target_node_id", None)
                target = next(
                    (
                        hotspot
                        for hotspot in hotspots
                        if getattr(hotspot, "node_id", None) == target_node_id
                    ),
                    None,
                )
                if target is None:
                    target = next(
                        (
                            component
                            for component in components
                            if getattr(component, "node_id", None) == target_node_id
                        ),
                        None,
                    )
                target_stress = _coerce_autonomous_unit_float(
                    getattr(target, "stress", None),
                    0.0,
                )
                if target is None or target_stress < stress_threshold:
                    continue
                improvements.append(
                    {
                        "component": (
                            _coerce_autonomous_text(getattr(target, "file_path", None))
                            or "unknown"
                        ),
                        "stress": round(target_stress, 3),
                        "risk": (
                            _coerce_autonomous_text(getattr(target, "risk_level", None))
                            or "unknown"
                        ),
                        "recommended_action": (
                            _coerce_autonomous_text(getattr(candidate, "kind", None))
                            or "unknown"
                        ),
                        "delta_es": round(
                            _coerce_autonomous_delta(
                                getattr(candidate, "predicted_repo_es_delta", None),
                                0.0,
                            ),
                            3,
                        ),
                        "summary": (
                            _coerce_autonomous_text(
                                getattr(candidate, "description", None)
                            )
                            or ""
                        ),
                    }
                )

            # 3. Update repo health in DB
            try:
                await db.execute(
                    text(
                        "UPDATE repositories SET nfet_phase = :phase, es_score = :es, "
                        "last_analyzed = now() WHERE id = :rid"
                    ),
                    {"phase": phase_value, "es": es_score, "rid": repo_id},
                )
                await db.commit()
            except Exception as exc:
                logger.warning(
                    "Repo %s failed to persist autonomous health during autonomous run: %s",
                    _redact_autonomous_error(repo_id),
                    _redact_autonomous_error(exc),
                )
                return {
                    "status": "failed",
                    "repo_id": repo_id,
                    "reason": "health_update_failed",
                }

            logger.info(
                "Autonomous: %s — ES=%.3f, phase=%s, %d ranked improvements",
                _redact_autonomous_error(repo_full_name), es_score, phase_value,
                len(improvements),
            )

            return {
                "status": "completed",
                "repo_id": repo_id,
                "full_name": repo_full_name,
                "es_score": round(es_score, 3),
                "phase": phase_value,
                "high_stress_count": len(hotspots),
                "improvements": improvements,
            }

    return run_sync_task(_run())


@celery_app.task(
    name="codey.saas.tasks.autonomous.run_all_autonomous_repos",
    bind=True,
)
def run_all_autonomous_repos(self) -> dict:
    """Fan out autonomous runs for every enabled repository."""
    from codey.saas.database import async_session_factory

    async def _fan_out() -> dict:
        try:
            async with async_session_factory() as db:
                from sqlalchemy import text

                dispatch_limit = _coerce_autonomous_dispatch_limit(
                    os.environ.get("CODEY_AUTONOMOUS_DISPATCH_LIMIT")
                )
                rows = await db.execute(
                    text(
                        "SELECT id, user_id FROM repositories "
                        "WHERE autonomous_mode_enabled = true "
                        "ORDER BY last_analyzed ASC NULLS FIRST, "
                        "created_at ASC NULLS FIRST, id ASC "
                        "LIMIT :limit"
                    ),
                    {"limit": dispatch_limit},
                )
                repos = _coerce_autonomous_mapping_rows(rows.mappings().all())
        except Exception as exc:
            logger.warning(
                "Failed to load autonomous repositories for dispatch: %s",
                _redact_autonomous_error(exc),
            )
            return {
                "status": "failed",
                "reason": "repo_query_failed",
                "dispatched": 0,
            }

        dispatched = 0
        for repo in repos:
            repo_id = _coerce_autonomous_identifier(repo.get("id"))
            user_id = _coerce_autonomous_identifier(repo.get("user_id"))
            if not repo_id or not user_id:
                logger.warning(
                    "Skipping autonomous dispatch for malformed repo row: %s",
                    _redact_autonomous_error(repo),
                )
                continue
            try:
                run_autonomous_repo.apply_async(
                    args=[repo_id, user_id],
                    queue="autonomous",
                )
                dispatched += 1
            except Exception as exc:
                logger.warning(
                    "Failed to dispatch autonomous run for repo %s: %s",
                    _redact_autonomous_error(repo_id),
                    _redact_autonomous_error(exc),
                )

        logger.info("Dispatched autonomous runs for %d repos", dispatched)
        return {"dispatched": dispatched}

    return run_sync_task(_fan_out())
