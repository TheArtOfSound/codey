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

            # TODO: plug in full build pipeline —
            #   1. retrieve planned files for this phase
            #   2. generate code via code_agent
            #   3. validate via sandbox execution
            #   4. record build_files + build_checkpoint
            #   5. charge credits

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
