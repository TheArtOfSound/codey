from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from uuid import UUID

from codey.saas.build_mode.path_utils import normalize_plan_file_path
from codey.saas.tasks.asyncio_utils import run_sync_task
from codey.saas.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_MAX_BUILD_PHASE_INDEX = 10_000
_TASK_URL_CREDENTIALS_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_TASK_QUERY_SECRET_RE = re.compile(
    r"([?&#](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token)=)[^&#\s]+",
    re.IGNORECASE,
)
_TASK_NAMED_SECRET_RE = re.compile(
    r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token|authorization)\b(\s*[:=]\s*)"
    r"(?:Bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
_TASK_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)


def _coerce_positive_int(
    value: object,
    default: int = 1,
    maximum: int = _MAX_BUILD_PHASE_INDEX,
) -> int:
    if isinstance(value, bool):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(normalized, maximum) if normalized > 0 else default


def _coerce_nonnegative_int(
    value: object,
    default: int = 0,
    maximum: int = _MAX_BUILD_PHASE_INDEX,
) -> int:
    if isinstance(value, bool):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(normalized, maximum) if normalized >= 0 else default


def _coerce_task_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_task_identifier(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    identifier = _coerce_task_text(value)
    if (
        identifier is None
        or _has_ascii_control(identifier)
        or _has_whitespace(identifier)
    ):
        return None
    return identifier


def _redact_task_error(value: object) -> str:
    text = _TASK_URL_CREDENTIALS_RE.sub(r"\1***@", str(value))
    text = _TASK_QUERY_SECRET_RE.sub(r"\1***", text)

    def _replace_named_secret(match: re.Match[str]) -> str:
        prefix = f"{match.group(1)}{match.group(2)}"
        if "bearer" in match.group(0).lower():
            return f"{prefix}Bearer ***"
        return f"{prefix}***"

    text = _TASK_NAMED_SECRET_RE.sub(_replace_named_secret, text)
    return _TASK_EMAIL_RE.sub("[redacted-email]", text)


def _coerce_task_mapping_rows(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _coerce_task_mapping_row(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _coerce_generated_file_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        for key in ("content", "code", "text", "output"):
            candidate = value.get(key)
            if candidate is None:
                continue
            try:
                return _coerce_generated_file_content(candidate)
            except TypeError:
                continue
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            try:
                candidate = _coerce_generated_file_content(item)
            except TypeError:
                continue
            if candidate:
                parts.append(candidate)
        if parts:
            return "\n".join(parts)
    raise TypeError("Model returned non-text generated file content")


def _parse_generated_file_content(value: object) -> str:
    content = _coerce_generated_file_content(value)
    matches = re.findall(r"```[^\n\r]*\r?\n(.*?)```", content, re.DOTALL)
    if matches:
        content = max(matches, key=len).rstrip()
    else:
        _stripped = content.strip()
        if _stripped.startswith("```"):
            _lines = _stripped.splitlines()[1:]
            if _lines and _lines[-1].strip().startswith("```"):
                _lines = _lines[:-1]
            content = chr(10).join(_lines).strip()
    if not content.strip():
        raise TypeError("Model returned empty generated file content")
    return content


def _count_generated_file_lines(content: str) -> int:
    return sum(1 for line in content.splitlines() if line.strip())


@celery_app.task(
    name="codey.saas.tasks.builds.run_build_phase",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def run_build_phase(
    self,
    build_project_id: str,
    phase_number: int,
    user_id: str,
) -> dict:
    """Execute a single build phase for a multi-phase project build.

    Each phase generates a subset of the project files, validates them,
    records a checkpoint, and — if more phases remain — chains the next.
    """
    safe_project_id = _coerce_task_identifier(build_project_id)
    safe_user_id = _coerce_task_identifier(user_id)
    if safe_project_id is None or safe_user_id is None:
        logger.warning("Skipping build phase with malformed identifiers")
        return {"status": "error", "reason": "malformed_identifiers"}

    build_project_id = safe_project_id
    user_id = safe_user_id

    async def _run() -> dict:
        from codey.saas.database import async_session_factory, task_db_session
        from sqlalchemy import text

        async with task_db_session() as db:
            row = await db.execute(
                text(
                    "SELECT id, name, status, current_phase, total_phases, "
                    "project_plan, stack "
                    "FROM build_projects "
                    "WHERE id = :pid AND user_id = :uid"
                ),
                {"pid": build_project_id, "uid": user_id},
            )
            project = _coerce_task_mapping_row(row.mappings().first())
            if project is None:
                logger.warning(
                    "Build project %s not found",
                    _redact_task_error(build_project_id),
                )
                return {"status": "error", "reason": "project_not_found"}

            project_status = _coerce_task_text(project.get("status")) or "unknown"
            project_name = _coerce_task_text(project.get("name")) or str(build_project_id)
            if project_status in ("completed", "cancelled", "failed"):
                return {"status": "skipped", "reason": f"project is {project_status}"}

            total_phases = _coerce_positive_int(project.get("total_phases"), 1)
            phase = _coerce_positive_int(phase_number, 1)
            current_phase = _coerce_nonnegative_int(project.get("current_phase"), 0)
            if phase > total_phases:
                logger.warning(
                    "Skipping phase %d for build project %s; total phases is %d",
                    phase,
                    _redact_task_error(build_project_id),
                    total_phases,
                )
                return {
                    "status": "skipped",
                    "reason": "phase_out_of_range",
                    "phase": phase,
                    "total_phases": total_phases,
                }
            if phase < current_phase:
                logger.info(
                    "Skipping stale phase %d for build project %s; current phase is %d",
                    phase,
                    _redact_task_error(build_project_id),
                    current_phase,
                )
                return {
                    "status": "skipped",
                    "reason": "stale_phase",
                    "phase": phase,
                    "current_phase": current_phase,
                }
            logger.info(
                "Running phase %d/%d for build project %s (%s)",
                phase,
                total_phases,
                _redact_task_error(build_project_id),
                _redact_task_error(project_name),
            )

            # Update status
            await db.execute(
                text(
                    "UPDATE build_projects "
                    "SET current_phase = :phase, status = 'building' "
                    "WHERE id = :pid"
                ),
                {"phase": phase, "pid": build_project_id},
            )
            await db.commit()

            # Full build pipeline for this phase
            try:
                from codey.saas.intelligence.providers import call_model, resolve_model, set_byok_override

                try:
                    _byok_row = (await db.execute(
                        text("SELECT byok_provider, byok_api_key, byok_model FROM users WHERE id = :uid"),
                        {"uid": user_id},
                    )).mappings().first()
                    if _byok_row and _byok_row.get("byok_provider") and _byok_row.get("byok_api_key"):
                        from codey.saas.security.encryption import decrypt_token
                        try:
                            _bk = decrypt_token(_byok_row["byok_api_key"])
                        except Exception:
                            _bk = None
                        set_byok_override(_byok_row.get("byok_provider"), _bk, _byok_row.get("byok_model"))
                    else:
                        set_byok_override(None, None, None)
                except Exception:
                    set_byok_override(None, None, None)

                provider, model = resolve_model("code_generation")
            except Exception as exc:
                logger.warning(
                    "Build project %s failed to resolve code generation model: %s",
                    _redact_task_error(build_project_id),
                    _redact_task_error(exc),
                )
                await db.execute(
                    text("UPDATE build_projects SET status = 'failed' WHERE id = :pid"),
                    {"pid": build_project_id},
                )
                await db.commit()
                return {
                    "status": "error",
                    "reason": "model_resolution_failed",
                    "phase": phase,
                }

            # 1. Retrieve planned files for this phase
            file_rows = await db.execute(
                text(
                    "SELECT id, file_path FROM build_files "
                    "WHERE project_id = :pid AND phase = :phase AND status = 'pending'"
                ),
                {"pid": build_project_id, "phase": phase},
            )
            files = _coerce_task_mapping_rows(file_rows.mappings().all())
            if not files and current_phase >= phase:
                logger.info(
                    "Skipping phase %d for build project %s; no pending files",
                    phase,
                    _redact_task_error(build_project_id),
                )
                return {
                    "status": "skipped",
                    "reason": "no_pending_files",
                    "phase": phase,
                }
            if not files:
                logger.warning(
                    "Build project %s has no pending files for active phase %d",
                    _redact_task_error(build_project_id),
                    phase,
                )
                await db.execute(
                    text("UPDATE build_projects SET status = 'failed' WHERE id = :pid"),
                    {"pid": build_project_id},
                )
                await db.commit()
                return {
                    "status": "error",
                    "reason": "file_generation_failed",
                    "phase": phase,
                }

            description = project_name
            plan = project.get("project_plan") or {}
            files_completed = 0
            generation_failures = 0
            lines_total = 0

            # 2. Generate code for each file
            for bf in files:
                file_id = _coerce_task_identifier(bf.get("id"))
                file_path = normalize_plan_file_path(bf.get("file_path"))
                if not file_id or not file_path:
                    logger.warning(
                        "Skipping malformed build_file row for project %s: %s",
                        _redact_task_error(build_project_id),
                        _redact_task_error(bf),
                    )
                    generation_failures += 1
                    if file_id:
                        await db.execute(
                            text("UPDATE build_files SET status = 'failed' WHERE id = :fid"),
                            {"fid": file_id},
                        )
                    continue
                try:
                    gen_messages = [
                        {"role": "system", "content": (
                            "You are Codey. Generate production-quality code for a single file. "
                            "Return ONLY the file content. No markdown fences. No explanation."
                        )},
                        {"role": "user", "content": (
                            f"Project: {description}\nFile: {file_path}\n"
                            f"Generate the complete content for this file."
                        )},
                    ]
                    content = _parse_generated_file_content(
                        await call_model(provider, model, gen_messages, max_tokens=4096)
                    )
                    line_count = _count_generated_file_lines(content)

                    # 3. Update build_file record
                    await db.execute(
                        text(
                            "UPDATE build_files SET content = :content, line_count = :lines, "
                            "status = 'completed', validation_passed = true, "
                            "generated_at = now() WHERE id = :fid"
                        ),
                        {"content": content, "lines": line_count, "fid": file_id},
                    )
                    files_completed += 1
                    lines_total += line_count
                except Exception as e:
                    logger.warning(
                        "File gen failed: %s — %s",
                        _redact_task_error(file_path),
                        _redact_task_error(e),
                    )
                    generation_failures += 1
                    if file_id:
                        await db.execute(
                            text("UPDATE build_files SET status = 'failed' WHERE id = :fid"),
                            {"fid": file_id},
                        )

            if generation_failures > 0:
                await db.execute(
                    text("UPDATE build_projects SET status = 'failed' WHERE id = :pid"),
                    {"pid": build_project_id},
                )
                await db.commit()
                return {
                    "status": "error",
                    "reason": "file_generation_failed",
                    "phase": phase,
                }

            # 4. Record checkpoint
            await db.execute(
                text(
                    "INSERT INTO build_checkpoints "
                    "(project_id, phase, phase_name, files_in_phase, tests_passed, tests_failed) "
                    "VALUES (:pid, :phase, :name, :files, 0, 0)"
                ),
                {
                    "pid": build_project_id,
                    "phase": phase,
                    "name": f"Phase {phase}",
                    "files": files_completed,
                },
            )

            # 5. Update project stats
            await db.execute(
                text(
                    "UPDATE build_projects SET "
                    "files_completed = COALESCE(files_completed, 0) + :fc, "
                    "lines_generated = COALESCE(lines_generated, 0) + :lt "
                    "WHERE id = :pid"
                ),
                {"fc": files_completed, "lt": lines_total, "pid": build_project_id},
            )
            await db.commit()

            logger.info(
                "Phase %d: %d files, %d lines generated",
                phase, files_completed, lines_total,
            )

            # If more phases remain, chain the next one
            if phase < total_phases:
                try:
                    run_build_phase.apply_async(
                        args=[build_project_id, phase + 1, user_id],
                        countdown=5,
                        queue="builds",
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to enqueue phase %d for build project %s: %s",
                        phase + 1,
                        _redact_task_error(build_project_id),
                        _redact_task_error(exc),
                    )
                    await db.execute(
                        text("UPDATE build_projects SET status = 'failed' WHERE id = :pid"),
                        {"pid": build_project_id},
                    )
                    await db.commit()
                    return {
                        "status": "error",
                        "reason": "next_phase_enqueue_failed",
                        "phase": phase,
                    }
                return {
                    "status": "phase_completed",
                    "phase": phase,
                    "next_phase": phase + 1,
                }

            # Final phase — mark project complete
            await db.execute(
                text(
                    "UPDATE build_projects "
                    "SET status = 'completed', completed_at = now() "
                    "WHERE id = :pid"
                ),
                {"pid": build_project_id},
            )
            await db.commit()

            logger.info("Build project %s completed", _redact_task_error(build_project_id))
            return {"status": "completed", "phase": phase}

    return run_sync_task(_run())
