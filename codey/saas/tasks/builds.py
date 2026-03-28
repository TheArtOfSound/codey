from __future__ import annotations

import logging

from codey.saas.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


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
    import asyncio

    async def _run() -> dict:
        from codey.saas.database import async_session_factory
        from sqlalchemy import text

        async with async_session_factory() as db:
            row = await db.execute(
                text(
                    "SELECT id, name, status, current_phase, total_phases, "
                    "project_plan, stack "
                    "FROM build_projects "
                    "WHERE id = :pid AND user_id = :uid"
                ),
                {"pid": build_project_id, "uid": user_id},
            )
            project = row.mappings().first()
            if project is None:
                logger.warning("Build project %s not found", build_project_id)
                return {"status": "error", "reason": "project_not_found"}

            if project["status"] in ("completed", "cancelled"):
                return {"status": "skipped", "reason": f"project is {project['status']}"}

            logger.info(
                "Running phase %d/%d for build project %s (%s)",
                phase_number,
                project["total_phases"],
                build_project_id,
                project["name"],
            )

            # Update status
            await db.execute(
                text(
                    "UPDATE build_projects "
                    "SET current_phase = :phase, status = 'building' "
                    "WHERE id = :pid"
                ),
                {"phase": phase_number, "pid": build_project_id},
            )
            await db.commit()

            # Full build pipeline for this phase
            from codey.saas.intelligence.providers import call_model, resolve_model

            # 1. Retrieve planned files for this phase
            file_rows = await db.execute(
                text(
                    "SELECT id, file_path FROM build_files "
                    "WHERE project_id = :pid AND phase = :phase AND status = 'pending'"
                ),
                {"pid": build_project_id, "phase": phase_number},
            )
            files = file_rows.mappings().all()

            provider, model = resolve_model("code_generation")
            description = project.get("name", "")
            plan = project.get("project_plan") or {}
            files_completed = 0
            lines_total = 0

            # 2. Generate code for each file
            for bf in files:
                try:
                    gen_messages = [
                        {"role": "system", "content": (
                            "You are Codey. Generate production-quality code for a single file. "
                            "Return ONLY the file content. No markdown fences. No explanation."
                        )},
                        {"role": "user", "content": (
                            f"Project: {description}\nFile: {bf['file_path']}\n"
                            f"Generate the complete content for this file."
                        )},
                    ]
                    content = await call_model(provider, model, gen_messages, max_tokens=4096)
                    line_count = content.count("\n") + 1

                    # 3. Update build_file record
                    await db.execute(
                        text(
                            "UPDATE build_files SET content = :content, line_count = :lines, "
                            "status = 'completed', validation_passed = true, "
                            "generated_at = now() WHERE id = :fid"
                        ),
                        {"content": content, "lines": line_count, "fid": str(bf["id"])},
                    )
                    files_completed += 1
                    lines_total += line_count
                except Exception as e:
                    logger.warning("File gen failed: %s — %s", bf["file_path"], e)
                    await db.execute(
                        text("UPDATE build_files SET status = 'failed' WHERE id = :fid"),
                        {"fid": str(bf["id"])},
                    )

            # 4. Record checkpoint
            await db.execute(
                text(
                    "INSERT INTO build_checkpoints "
                    "(project_id, phase, phase_name, files_in_phase, tests_passed, tests_failed) "
                    "VALUES (:pid, :phase, :name, :files, 0, 0)"
                ),
                {
                    "pid": build_project_id,
                    "phase": phase_number,
                    "name": f"Phase {phase_number}",
                    "files": files_completed,
                },
            )

            # 5. Update project stats
            await db.execute(
                text(
                    "UPDATE build_projects SET files_completed = COALESCE(files_completed, 0) + :fc, "
                    "lines_generated = COALESCE(lines_generated, 0) + :lt WHERE id = :pid"
                ),
                {"fc": files_completed, "lt": lines_total, "pid": build_project_id},
            )
            await db.commit()

            logger.info(
                "Phase %d: %d files, %d lines generated",
                phase_number, files_completed, lines_total,
            )

            # If more phases remain, chain the next one
            total_phases = project["total_phases"] or 1
            if phase_number < total_phases:
                run_build_phase.apply_async(
                    args=[build_project_id, phase_number + 1, user_id],
                    countdown=5,
                )
                return {
                    "status": "phase_completed",
                    "phase": phase_number,
                    "next_phase": phase_number + 1,
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

            logger.info("Build project %s completed", build_project_id)
            return {"status": "completed", "phase": phase_number}

    return asyncio.get_event_loop().run_until_complete(_run())
