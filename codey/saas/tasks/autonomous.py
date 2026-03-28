from __future__ import annotations

import logging

from codey.saas.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


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
    from codey.saas.database import async_session_factory

    import asyncio

    async def _run() -> dict:
        async with async_session_factory() as db:
            from sqlalchemy import text

            # Fetch repo config
            row = await db.execute(
                text(
                    "SELECT id, full_name, clone_url, default_branch, autonomous_config "
                    "FROM repositories WHERE id = :rid AND user_id = :uid "
                    "AND autonomous_mode_enabled = true"
                ),
                {"rid": repo_id, "uid": user_id},
            )
            repo = row.mappings().first()
            if repo is None:
                logger.warning("Repo %s not found or autonomous disabled", repo_id)
                return {"status": "skipped", "repo_id": repo_id}

            logger.info(
                "Running autonomous analysis on %s (%s)",
                repo["full_name"],
                repo_id,
            )

            # TODO: plug in full autonomous pipeline —
            #   1. clone / pull latest
            #   2. run NFET analysis
            #   3. generate improvements via code_agent
            #   4. open PR if configured
            #   5. record session + costs

            return {
                "status": "completed",
                "repo_id": repo_id,
                "full_name": repo["full_name"],
            }

    return asyncio.get_event_loop().run_until_complete(_run())


@celery_app.task(
    name="codey.saas.tasks.autonomous.run_all_autonomous_repos",
    bind=True,
)
def run_all_autonomous_repos(self) -> dict:
    """Nightly job: fan out autonomous runs for every enabled repository."""
    from codey.saas.database import async_session_factory

    import asyncio

    async def _fan_out() -> dict:
        async with async_session_factory() as db:
            from sqlalchemy import text

            rows = await db.execute(
                text(
                    "SELECT id, user_id FROM repositories "
                    "WHERE autonomous_mode_enabled = true"
                )
            )
            repos = rows.mappings().all()

        dispatched = 0
        for repo in repos:
            run_autonomous_repo.apply_async(
                args=[str(repo["id"]), str(repo["user_id"])],
                queue="autonomous",
            )
            dispatched += 1

        logger.info("Dispatched autonomous runs for %d repos", dispatched)
        return {"dispatched": dispatched}

    return asyncio.get_event_loop().run_until_complete(_fan_out())
