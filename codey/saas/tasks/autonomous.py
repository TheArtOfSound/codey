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

            import tempfile
            import subprocess
            import shutil
            from pathlib import Path
            from codey.parser.extractor import extract_from_directory
            from codey.graph.engine import CodebaseGraph
            from codey.nfet.sweep import NFETSweep

            config = repo.get("autonomous_config") or {}
            stress_threshold = config.get("stress_trigger", 0.7)

            # 1. Clone repo
            clone_dir = Path(tempfile.mkdtemp(prefix="codey_auto_"))
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", repo["clone_url"], str(clone_dir)],
                    capture_output=True, timeout=120, check=True,
                )

                # 2. Parse + NFET analysis
                nodes, edges = extract_from_directory(str(clone_dir))
                graph = CodebaseGraph()
                graph.build_from_nodes_edges(nodes, edges)
                sweep = NFETSweep()
                result = sweep.run(graph)

                # 3. Find high-stress components and suggest fixes
                high_stress = graph.get_high_stress_components(threshold=stress_threshold)
                improvements = []

                if high_stress:
                    from codey.llm.code_agent import CodeAgent
                    agent = CodeAgent(graph, sweep)

                    for comp_id, stress_val in high_stress[:3]:
                        try:
                            suggestion = agent.suggest_refactor(comp_id)
                            improvements.append({
                                "component": comp_id,
                                "stress": round(stress_val, 3),
                                "suggestions": suggestion.get("suggestions", [])[:2],
                            })
                        except Exception as e:
                            logger.warning("Refactor failed for %s: %s", comp_id, e)

                # 4. Update repo health in DB
                await db.execute(
                    text(
                        "UPDATE repositories SET nfet_phase = :phase, es_score = :es, "
                        "last_analyzed = now() WHERE id = :rid"
                    ),
                    {"phase": result.phase.value, "es": result.es_score, "rid": repo_id},
                )
                await db.commit()

                logger.info(
                    "Autonomous: %s — ES=%.3f, phase=%s, %d improvements",
                    repo["full_name"], result.es_score, result.phase.value,
                    len(improvements),
                )

            finally:
                shutil.rmtree(clone_dir, ignore_errors=True)

            return {
                "status": "completed",
                "repo_id": repo_id,
                "full_name": repo["full_name"],
                "es_score": round(result.es_score, 3),
                "phase": result.phase.value,
                "high_stress_count": len(high_stress),
                "improvements": improvements,
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
