"""SessionRunner -- executes coding sessions and streams output in real time."""

from __future__ import annotations

import logging
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.graph.engine import CodebaseGraph
from codey.llm.code_agent import CodeAgent
from codey.llm.prompt_builder import PromptBuilder
from codey.nfet.sweep import NFETSweep, SweepResult
from codey.parser.extractor import LanguageParser, parse_directory
from codey.saas.credits.service import CREDIT_COSTS, CreditService, InsufficientCreditsError
from codey.saas.models import CodingSession, Repository
from codey.saas.sessions.stream import SessionStream

logger = logging.getLogger(__name__)


class SessionRunner:
    """Executes coding sessions against a codebase graph and streams results.

    All public methods are designed to be called from an async task runner.
    Errors at any stage are caught, streamed to connected clients, and credits
    are refunded so the user is never charged for a failed session.
    """

    def __init__(self, stream: SessionStream) -> None:
        self._stream = stream

    # ------------------------------------------------------------------
    # Prompt-based code generation session
    # ------------------------------------------------------------------

    async def run_prompt_session(
        self,
        session_id: str,
        user_id: str,
        prompt: str,
        language: str | None,
        repo_id: str | None,
        db: AsyncSession,
    ) -> None:
        """Execute a full prompt-based coding session.

        Pipeline
        --------
        1. Update session status, send "Starting session..."
        2. If *repo_id* is provided, load the repository, parse it, build graph.
        3. Run an NFET sweep on the graph and stream the scan results.
        4. Build a structural prompt and stream the execution plan.
        5. Call the LLM to generate code; stream code chunks + explanation.
        6. Run post-generation NFET sweep and stream the after metrics.
        7. Calculate actual credit cost; adjust charges if needed.
        8. Persist results to the CodingSession record and stream ``complete``.

        On any error the session is marked failed, credits are refunded, and
        the error is streamed to connected clients.
        """
        sid = UUID(session_id)
        uid = UUID(user_id)
        credit_svc = CreditService(db)
        reserved_credits = 0

        try:
            # ----- 1. Mark session as running -----
            session = await self._get_session(db, sid)
            session.status = "running"
            await db.flush()

            await self._send(session_id, {"type": "status", "message": "Starting session..."})

            # ----- 2. Build codebase graph -----
            graph = CodebaseGraph()
            sweep = NFETSweep()

            if repo_id:
                await self._send(session_id, {
                    "type": "status",
                    "message": "Analyzing codebase structure...",
                })
                repo = await self._get_repository(db, UUID(repo_id))
                nodes, edges = await self._parse_repository(repo)
                graph.build_from_nodes_edges(nodes, edges)
                sweep.calibrate(graph)
            else:
                # No repo -- create a minimal graph from the prompt context
                await self._send(session_id, {
                    "type": "status",
                    "message": "Preparing generation context...",
                })

            # ----- 3. Pre-generation NFET sweep -----
            before_result: SweepResult | None = None
            if graph.node_count > 0:
                before_result = sweep.run(graph)
                await self._send(session_id, {
                    "type": "nfet_scan",
                    "phase": before_result.phase.value.upper(),
                    "kappa": round(before_result.kappa, 3),
                    "sigma": round(before_result.sigma, 3),
                    "es": round(before_result.es_score, 3),
                })
                session.nfet_phase_before = before_result.phase.value
                session.es_score_before = before_result.es_score

            # ----- 4. Build structural prompt + plan -----
            if graph.node_count > 0:
                builder = PromptBuilder(graph, sweep)
                plan_result = sweep.run(graph)
                context = builder.build_context(plan_result)
                plan_steps = self._derive_plan_steps(prompt, language, context)
            else:
                plan_steps = self._derive_plan_steps(prompt, language, None)

            await self._send(session_id, {"type": "plan", "steps": plan_steps})

            # ----- 5. Generate code via LLM -----
            await self._send(session_id, {
                "type": "status",
                "message": "Generating code...",
            })

            agent = CodeAgent(graph, sweep) if graph.node_count > 0 else CodeAgent(
                CodebaseGraph(), NFETSweep()
            )
            result = agent.generate_code(prompt)

            code = result.get("code", "")
            explanation = result.get("explanation", "")

            # Stream code chunks -- split by file if the output contains
            # multiple file markers, otherwise send as a single chunk.
            files_generated = self._split_code_into_files(code, language)
            for file_path, file_content in files_generated.items():
                await self._send(session_id, {
                    "type": "code_chunk",
                    "file": file_path,
                    "content": file_content,
                })

            await self._send(session_id, {
                "type": "explanation",
                "content": explanation,
            })

            # ----- 6. Post-generation NFET sweep -----
            after_result: SweepResult | None = None
            if graph.node_count > 0 and before_result is not None:
                # Re-parse generated code into the graph for an accurate after sweep
                parser = LanguageParser()
                for file_path, file_content in files_generated.items():
                    with tempfile.NamedTemporaryFile(
                        suffix=Path(file_path).suffix or ".py",
                        mode="w",
                        delete=False,
                    ) as tmp:
                        tmp.write(file_content)
                        tmp.flush()
                        tmp_path = Path(tmp.name)
                    try:
                        new_nodes, new_edges = parser.parse_file(tmp_path)
                        # Remap file_path in parsed nodes to the real target path
                        for node in new_nodes:
                            node.file_path = file_path
                        graph.update_file(file_path, new_nodes, new_edges)
                    finally:
                        tmp_path.unlink(missing_ok=True)

                after_result = sweep.run(graph)
                await self._send(session_id, {
                    "type": "nfet_after",
                    "phase": after_result.phase.value.upper(),
                    "kappa": round(after_result.kappa, 3),
                    "sigma": round(after_result.sigma, 3),
                    "es": round(after_result.es_score, 3),
                })
                session.nfet_phase_after = after_result.phase.value
                session.es_score_after = after_result.es_score

            # ----- 7. Calculate and adjust credits -----
            total_lines = self._count_lines(code)
            actual_cost = self._determine_credit_cost(total_lines)

            estimated_cost = CreditService.estimate_cost(prompt, "prompt")
            # Cap at the estimated cost to be user-friendly
            charged = min(actual_cost, estimated_cost)

            try:
                tx = await credit_svc.reserve_credits(
                    uid, charged, f"Session {session_id}: {total_lines} lines generated", sid
                )
                reserved_credits = charged
            except InsufficientCreditsError:
                # If they can't afford it, charge what they have
                balance = await credit_svc.get_balance(uid)
                available = balance["total"]
                if available > 0:
                    tx = await credit_svc.reserve_credits(
                        uid, available, f"Session {session_id}: partial charge", sid
                    )
                    reserved_credits = available
                # Still deliver the results -- the code was already generated

            # ----- 8. Persist results -----
            files_modified = len(files_generated)
            session.status = "completed"
            session.credits_charged = reserved_credits
            session.lines_generated = total_lines
            session.files_modified = files_modified
            session.output_summary = explanation[:500] if explanation else None
            session.completed_at = datetime.now(timezone.utc)
            await db.flush()
            await db.commit()

            await self._send(session_id, {
                "type": "complete",
                "credits_charged": reserved_credits,
                "lines_generated": total_lines,
                "files_modified": files_modified,
            })

        except Exception as exc:
            logger.exception("Session %s failed: %s", session_id, exc)
            await self._handle_failure(
                db, sid, uid, reserved_credits, credit_svc, session_id, exc
            )

    # ------------------------------------------------------------------
    # Analysis session (file upload + NFET)
    # ------------------------------------------------------------------

    async def run_analyze_session(
        self,
        session_id: str,
        user_id: str,
        file_paths: list[str],
        db: AsyncSession,
    ) -> None:
        """Execute a codebase analysis session.

        Pipeline
        --------
        1. Parse uploaded files into a codebase graph.
        2. Run an NFET sweep.
        3. Stream scan results and structural health explanation.
        4. Identify top stress components and send recommendations.
        5. Persist results to the CodingSession record.
        """
        sid = UUID(session_id)
        uid = UUID(user_id)
        credit_svc = CreditService(db)
        reserved_credits = 0

        try:
            session = await self._get_session(db, sid)
            session.status = "running"
            await db.flush()

            await self._send(session_id, {
                "type": "status",
                "message": "Parsing uploaded files...",
            })

            # ----- 1. Parse files -----
            graph = CodebaseGraph()
            parser = LanguageParser()
            all_nodes = []
            all_edges = []

            for fp in file_paths:
                path = Path(fp)
                if path.is_dir():
                    dir_nodes, dir_edges = parse_directory(path)
                    all_nodes.extend(dir_nodes)
                    all_edges.extend(dir_edges)
                elif path.is_file():
                    file_nodes, file_edges = parser.parse_file(path)
                    all_nodes.extend(file_nodes)
                    all_edges.extend(file_edges)
                else:
                    logger.warning("Skipping non-existent path: %s", fp)

            graph.build_from_nodes_edges(all_nodes, all_edges)

            await self._send(session_id, {
                "type": "status",
                "message": f"Parsed {graph.node_count} components, {graph.edge_count} dependencies.",
            })

            # ----- 2. NFET sweep -----
            sweep = NFETSweep()
            sweep.calibrate(graph)
            result = sweep.run(graph)

            await self._send(session_id, {
                "type": "nfet_scan",
                "phase": result.phase.value.upper(),
                "kappa": round(result.kappa, 3),
                "sigma": round(result.sigma, 3),
                "es": round(result.es_score, 3),
            })

            # ----- 3. Structural health explanation -----
            health_summary = self._build_health_explanation(result)
            await self._send(session_id, {
                "type": "explanation",
                "content": health_summary,
            })

            # ----- 4. Top stress components + recommendations -----
            recommendations: list[str] = []
            for comp_id, stress_val in result.top_stress_components[:5]:
                comp_data = graph._graph.nodes.get(comp_id)
                name = comp_data.get("name", comp_id) if comp_data else comp_id
                file_path = comp_data.get("file_path", "") if comp_data else ""
                coupling = graph.coupling_score(comp_id)
                cascade = graph.cascade_depth(comp_id)

                rec = (
                    f"{name} ({file_path}): stress={stress_val:.2f}, "
                    f"coupling={coupling:.1f}, cascade_depth={cascade}. "
                )
                if stress_val > 0.7:
                    rec += "HIGH RISK -- consider extracting shared logic to reduce coupling."
                elif stress_val > 0.4:
                    rec += "Moderate risk -- monitor for coupling growth."
                else:
                    rec += "Healthy range."
                recommendations.append(rec)

            if recommendations:
                await self._send(session_id, {
                    "type": "plan",
                    "steps": recommendations,
                })

            # ----- 5. Credits + persist -----
            estimated_cost = CREDIT_COSTS["file_analysis"]
            try:
                await credit_svc.reserve_credits(
                    uid, estimated_cost, f"Analysis session {session_id}", sid
                )
                reserved_credits = estimated_cost
            except InsufficientCreditsError:
                logger.warning("User %s has insufficient credits for analysis", user_id)

            session.status = "completed"
            session.credits_charged = reserved_credits
            session.nfet_phase_before = result.phase.value
            session.es_score_before = result.es_score
            session.lines_generated = 0
            session.files_modified = 0
            session.output_summary = health_summary[:500]
            session.completed_at = datetime.now(timezone.utc)
            await db.flush()
            await db.commit()

            await self._send(session_id, {
                "type": "complete",
                "credits_charged": reserved_credits,
                "lines_generated": 0,
                "files_modified": 0,
            })

        except Exception as exc:
            logger.exception("Analysis session %s failed: %s", session_id, exc)
            await self._handle_failure(
                db, sid, uid, reserved_credits, credit_svc, session_id, exc
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send(self, session_id: str, message: dict) -> None:
        """Send a message to all connected clients, swallowing transport errors."""
        try:
            await self._stream.send_to_session(session_id, message)
        except Exception:
            logger.debug("Failed to send WS message for session %s", session_id)

    async def _get_session(self, db: AsyncSession, session_id: UUID) -> CodingSession:
        """Load a CodingSession row or raise."""
        result = await db.execute(
            select(CodingSession).where(CodingSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise ValueError(f"CodingSession {session_id} not found")
        return session

    async def _get_repository(self, db: AsyncSession, repo_id: UUID) -> Repository:
        """Load a Repository row or raise."""
        result = await db.execute(
            select(Repository).where(Repository.id == repo_id)
        )
        repo = result.scalar_one_or_none()
        if repo is None:
            raise ValueError(f"Repository {repo_id} not found")
        return repo

    async def _parse_repository(
        self, repo: Repository
    ) -> tuple[list, list]:
        """Clone/fetch a repository and parse its contents into nodes and edges.

        Returns (nodes, edges) suitable for ``CodebaseGraph.build_from_nodes_edges``.
        """
        clone_url = repo.clone_url
        if not clone_url:
            raise ValueError(f"Repository {repo.id} has no clone_url")

        # Clone into a temp directory
        import asyncio
        import shutil

        tmp_dir = Path(tempfile.mkdtemp(prefix="codey_repo_"))
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", clone_url, str(tmp_dir / "repo"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"git clone failed (exit {proc.returncode}): {stderr.decode()}"
                )

            repo_path = tmp_dir / "repo"
            nodes, edges = parse_directory(repo_path)
            return nodes, edges
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def _handle_failure(
        self,
        db: AsyncSession,
        session_id: UUID,
        user_id: UUID,
        reserved_credits: int,
        credit_svc: CreditService,
        ws_session_id: str,
        exc: Exception,
    ) -> None:
        """Mark a session as failed, refund credits, and stream the error."""
        error_msg = f"{type(exc).__name__}: {exc}"

        try:
            session = await self._get_session(db, session_id)
            session.status = "failed"
            session.error_message = error_msg[:1000]
            session.completed_at = datetime.now(timezone.utc)

            if reserved_credits > 0:
                await credit_svc.refund_credits(
                    user_id,
                    reserved_credits,
                    f"Refund for failed session {session_id}",
                    session_id,
                )

            await db.flush()
            await db.commit()
        except Exception as inner:
            logger.error(
                "Failed to persist failure state for session %s: %s",
                session_id,
                inner,
            )

        await self._send(ws_session_id, {
            "type": "error",
            "message": error_msg,
        })

    @staticmethod
    def _count_lines(code: str) -> int:
        """Count non-empty lines in a code string."""
        if not code:
            return 0
        return sum(1 for line in code.splitlines() if line.strip())

    @staticmethod
    def _determine_credit_cost(lines: int) -> int:
        """Map a line count to a credit cost using the standard tiers."""
        if lines < 50:
            return CREDIT_COSTS["simple_prompt"]
        if lines < 200:
            return CREDIT_COSTS["medium_prompt"]
        if lines < 500:
            return CREDIT_COSTS["large_prompt"]
        return CREDIT_COSTS["full_build"]

    @staticmethod
    def _derive_plan_steps(
        prompt: str, language: str | None, context: str | None
    ) -> list[str]:
        """Derive a human-readable plan from the prompt and structural context."""
        steps: list[str] = []

        # Heuristic plan derivation from prompt keywords
        prompt_lower = prompt.lower()

        if any(kw in prompt_lower for kw in ("import", "parse", "read", "load")):
            steps.append("Parse imports and dependencies")
        if any(kw in prompt_lower for kw in ("auth", "login", "jwt", "token", "oauth")):
            steps.append("Generate authentication module")
        if any(kw in prompt_lower for kw in ("api", "endpoint", "route", "handler")):
            steps.append("Build API endpoints")
        if any(kw in prompt_lower for kw in ("model", "schema", "database", "table")):
            steps.append("Define data models")
        if any(kw in prompt_lower for kw in ("test", "spec", "assert")):
            steps.append("Write test suite")
        if any(kw in prompt_lower for kw in ("refactor", "optimize", "improve")):
            steps.append("Analyze current structure for improvements")
        if any(kw in prompt_lower for kw in ("ui", "component", "page", "view", "template")):
            steps.append("Build UI components")

        # Always include core steps
        if not steps:
            steps.append("Analyze request requirements")

        lang_label = language or "target"
        steps.append(f"Generate {lang_label} code")

        if context:
            steps.append("Validate against NFET structural constraints")

        steps.append("Review and finalize output")
        return steps

    @staticmethod
    def _split_code_into_files(
        code: str, language: str | None
    ) -> dict[str, str]:
        """Split LLM output into per-file chunks.

        If the output contains file markers like ``# --- file: foo.py ---``
        or ``// --- file: foo.js ---``, split on those boundaries.  Otherwise
        return the entire output as a single file with a generated name.
        """
        import re

        files: dict[str, str] = {}
        # Match patterns like "# --- file: path/to/file.py ---" or
        # "// --- file: path/to/file.js ---"
        marker_re = re.compile(
            r"^(?:#|//) *--- *file: *(.+?) *---",
            re.MULTILINE,
        )

        markers = list(marker_re.finditer(code))
        if markers:
            for i, match in enumerate(markers):
                file_path = match.group(1).strip()
                start = match.end()
                end = markers[i + 1].start() if i + 1 < len(markers) else len(code)
                content = code[start:end].strip()
                if content:
                    files[file_path] = content
        else:
            # Single file output
            ext_map = {
                "python": ".py",
                "javascript": ".js",
                "typescript": ".ts",
                "jsx": ".jsx",
                "tsx": ".tsx",
            }
            ext = ext_map.get((language or "").lower(), ".py")
            files[f"generated{ext}"] = code.strip() if code else ""

        return files

    @staticmethod
    def _build_health_explanation(result: SweepResult) -> str:
        """Build a human-readable structural health summary from a sweep result."""
        phase_desc = {
            "ridge": "within its stability ridge -- structurally healthy",
            "caution": "in the caution zone -- some structural drift detected",
            "critical": "in a critical state -- significant structural degradation",
        }

        phase_text = phase_desc.get(result.phase.value, "in an unknown state")

        lines = [
            f"Codebase structural health: {phase_text}.",
            f"",
            f"Equilibrium Score (ES): {result.es_score:.3f}",
            f"Coupling density (kappa): {result.kappa:.3f}",
            f"Cascade margin (sigma): {result.sigma:.3f}",
            f"Total components: {result.total_nodes}",
            f"Total dependencies: {result.total_edges}",
            f"Mean coupling: {result.mean_coupling:.2f}",
            f"Mean cohesion: {result.mean_cohesion:.2f}",
        ]

        if result.highest_stress_component:
            lines.append(
                f"Highest stress: {result.highest_stress_component} "
                f"(stress={result.highest_stress_value:.2f})"
            )

        if result.phase.value == "critical":
            lines.append(
                "\nRecommendation: Prioritize decoupling high-stress components "
                "before adding new features."
            )
        elif result.phase.value == "caution":
            lines.append(
                "\nRecommendation: Monitor coupling growth and consider targeted "
                "refactoring of the top stress components."
            )
        else:
            lines.append(
                "\nThe codebase is healthy. Continue monitoring with NFET sweeps "
                "as the codebase evolves."
            )

        return "\n".join(lines)
