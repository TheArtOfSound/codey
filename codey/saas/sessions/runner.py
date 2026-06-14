"""SessionRunner -- executes coding sessions and streams output in real time."""

from __future__ import annotations

import logging
import tempfile
import traceback
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.graph.engine import CodebaseGraph
from codey.llm.code_agent import CodeAgent
from codey.nfet.repository_loader import (
    CLONE_TIMEOUT_SECONDS,
    _build_authenticated_clone_url,
    _clone_error_text,
    _git_clone_env,
    _terminate_timed_out_clone,
)
from codey.llm.prompt_builder import PromptBuilder
from codey.nfet.controller import NFETController
from codey.nfet.sweep import NFETSweep, SweepResult
from codey.parser.extractor import LanguageParser, parse_directory
from codey.saas.build_mode.path_utils import normalize_plan_file_path
from codey.saas.credits.service import CREDIT_COSTS, CreditService, InsufficientCreditsError
from codey.saas.models import CodingSession, Repository
from codey.saas.sessions.stream import SessionStream

logger = logging.getLogger(__name__)
SESSION_FAILURE_ERROR_LIMIT = 1000
_ALLOWED_REPOSITORY_CLONE_SCHEMES = {"git", "git+ssh", "http", "https", "ssh"}
_ALLOWED_REPOSITORY_SCP_CLONE_HOSTS = {"github.com", "www.github.com"}


def _session_failure_error_text(exc: Exception) -> str:
    return (
        f"{type(exc).__name__}: {_clone_error_text(str(exc), '')}"
    )[:SESSION_FAILURE_ERROR_LIMIT]


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_github_clone_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token or _has_ascii_control(token) or _has_whitespace(token):
        return None
    return token


def _coerce_repository_clone_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    clone_url = value.strip()
    if (
        not clone_url
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
            or host.lower() not in _ALLOWED_REPOSITORY_SCP_CLONE_HOSTS
        ):
            return None
    else:
        try:
            split = urlsplit(clone_url)
            port = split.port
        except ValueError:
            return None
        scheme = split.scheme.lower()
        if scheme not in _ALLOWED_REPOSITORY_CLONE_SCHEMES:
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


def _coerce_runner_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


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
        sid = _coerce_runner_uuid(session_id)
        uid = _coerce_runner_uuid(user_id)
        if sid is None or uid is None:
            logger.warning("Skipping prompt session with malformed identifiers")
            await self._send(session_id, {
                "type": "error",
                "message": "ValueError: Invalid session or user ID",
            })
            return
        credit_svc = CreditService(db)
        reserved_credits = 0

        try:
            # ----- 1. Mark session as running -----
            session = await self._get_session(db, sid, uid)
            session.status = "running"
            await db.flush()

            await self._send(session_id, {"type": "status", "message": "Starting session..."})

            # ----- 2. Build codebase graph -----
            graph = CodebaseGraph()
            sweep = NFETSweep()
            controller = NFETController(sweep_engine=sweep)

            if repo_id:
                await self._send(session_id, {
                    "type": "status",
                    "message": "Analyzing codebase structure...",
                })
                repo = await self._get_repository(db, UUID(repo_id), uid)
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

                repo_state = controller.analyze(graph, goal=prompt)
                candidates = controller.rank_interventions(
                    graph,
                    goal=prompt,
                    repo_state=repo_state,
                    limit=3,
                )
                await self._send(session_id, {
                    "type": "nfet_hotspots",
                    "hotspots": [hotspot.to_dict() for hotspot in repo_state.hotspots],
                })
                await self._send(session_id, {
                    "type": "nfet_candidates",
                    "candidates": [candidate.to_dict() for candidate in candidates],
                })

            # ----- 4. Build structural prompt + plan -----
            if graph.node_count > 0:
                builder = PromptBuilder(graph, sweep)
                plan_result = sweep.run(graph)
                context = builder.build_context(plan_result)
                guidance = controller.build_guidance(repo_state, candidates)
                plan_steps = self._derive_plan_steps(
                    prompt,
                    language,
                    f"{context}\n\n{guidance}",
                )
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
            result = self._coerce_generated_result(agent.generate_code(prompt))

            code = self._coerce_generated_text(result.get("code", ""))
            explanation = self._coerce_generated_text(result.get("explanation", ""))

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
                        suffix=self._generated_temp_suffix(file_path, language),
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

            reserved_credits = await self._reserve_prompt_credits(
                credit_svc,
                uid,
                sid,
                session_id,
                charged,
                total_lines,
            )

            # ----- 8. Verify claims + build a patch receipt (no fake completions) -----
            from uuid import uuid4

            from codey.saas.sessions.patch_receipt import (
                PatchReceipt,
                RunIntent,
                RunStatus,
                Validation,
                coarse_status,
                compute_diff,
                derive_run_status,
                extract_claims,
                sanitize_summary_for_no_change,
                score_run_health,
                verify_patch_claims,
            )

            # This prompt flow *proposes* code; it does not commit to the user's
            # git repo here, so the receipt is honest about that. A false claim
            # (explanation describing edits not present in the output) is
            # rejected regardless of intent.
            originals: dict[str, str] = {}
            diff_text, file_changes, diff_hash = compute_diff(originals, files_generated)
            result_content = "\n".join(files_generated.values())
            claims = extract_claims(explanation)
            verification = verify_patch_claims(
                claims,
                diff_text,
                [c.path for c in file_changes],
                result_content=result_content,
            )
            files_modified = len(files_generated)

            run_status = derive_run_status(
                RunIntent.PROPOSED_PATCH,
                files_modified,
                patch_applied=False,
                claim_verification_passed=verification.passed,
            )
            if not verification.passed:
                run_status = RunStatus.FAILED_VERIFICATION
            elif files_modified == 0:
                run_status = RunStatus.COMPLETED_NO_CHANGES

            validation = Validation(
                syntaxChecked=after_result is not None,
                claimVerificationPassed=verification.passed,
                patchApplied=False,
                filesModifiedCount=files_modified,
            )
            health = score_run_health(
                intent=RunIntent.PROPOSED_PATCH,
                patch_applied=False,
                claim_verification_passed=verification.passed,
                files_modified_count=files_modified,
                target_file_changed=None,
                validation=validation,
                summary_matches_diff=verification.passed,
                misleading_claims=not verification.passed,
            )

            receipt = PatchReceipt(
                receiptId=str(uuid4()),
                runId=str(sid),
                repoId=str(repo_id) if repo_id else None,
                intent=RunIntent.PROPOSED_PATCH,
                status=run_status,
                startedAt=(
                    session.started_at.isoformat()
                    if session.started_at
                    else datetime.utcnow().isoformat()
                ),
                completedAt=datetime.utcnow().isoformat(),
                filesRead=sorted(originals.keys()),
                filesChanged=file_changes,
                diffText=diff_text,
                diffHash=diff_hash,
                claimsMade=verification.checks,
                commandsRun=[],
                validation=validation,
                phases=[
                    {"phase": "generate", "ok": True},
                    {"phase": "compute_diff", "ok": True},
                    {"phase": "verify_claims", "ok": verification.passed},
                ],
                healthBefore=session.es_score_before,
                healthAfter=session.es_score_after,
                healthScore=health,
                finalSummary=(explanation or "")[:2000],
            )

            summary = explanation or ""
            if run_status is not RunStatus.COMPLETED_WITH_PATCH:
                summary = sanitize_summary_for_no_change(summary)

            # ----- 9. Persist results (status derived from reality, not the LLM) -----
            session.status = coarse_status(run_status)
            session.run_status = run_status.value
            session.verification_passed = verification.passed
            session.health_score = health
            session.patch_receipt = receipt.to_dict()
            session.credits_charged = reserved_credits
            session.lines_generated = total_lines
            session.files_modified = files_modified
            session.output_summary = summary[:500] if summary else None
            session.completed_at = datetime.utcnow()
            await db.flush()
            await db.commit()

            await self._send(session_id, {
                "type": "complete",
                "run_status": run_status.value,
                "verification_passed": verification.passed,
                "health_score": health,
                "credits_charged": reserved_credits,
                "lines_generated": total_lines,
                "files_modified": files_modified,
                "claim_mismatches": [
                    {"claim": c.claim, "reason": c.mismatchReason}
                    for c in verification.mismatches
                ],
                "patch_receipt": receipt.to_dict(),
            })

        except Exception as exc:
            logger.warning(
                "Session %s failed: %s",
                session_id,
                _session_failure_error_text(exc),
            )
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
        sid = _coerce_runner_uuid(session_id)
        uid = _coerce_runner_uuid(user_id)
        if sid is None or uid is None:
            logger.warning("Skipping analysis session with malformed identifiers")
            await self._send(session_id, {
                "type": "error",
                "message": "ValueError: Invalid session or user ID",
            })
            return
        credit_svc = CreditService(db)
        reserved_credits = 0

        try:
            session = await self._get_session(db, sid, uid)
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
            controller = NFETController(sweep_engine=sweep)
            repo_state = controller.analyze(graph, goal="analysis session")
            candidates = controller.rank_interventions(
                graph,
                goal="analysis session",
                repo_state=repo_state,
                limit=5,
            )

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
            for candidate in candidates:
                recommendations.append(
                    f"{candidate.title}: delta_ES={candidate.predicted_repo_es_delta:.3f}, "
                    f"target={candidate.target_file_path}. {candidate.description}"
                )

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
            session.completed_at = datetime.utcnow()
            await db.flush()
            await db.commit()

            await self._send(session_id, {
                "type": "complete",
                "credits_charged": reserved_credits,
                "lines_generated": 0,
                "files_modified": 0,
            })

        except Exception as exc:
            logger.warning(
                "Analysis session %s failed: %s",
                session_id,
                _session_failure_error_text(exc),
            )
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

    async def _get_session(
        self,
        db: AsyncSession,
        session_id: UUID,
        user_id: UUID,
    ) -> CodingSession:
        """Load a CodingSession row or raise."""
        result = await db.execute(
            select(CodingSession).where(
                CodingSession.id == session_id,
                CodingSession.user_id == user_id,
            )
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise ValueError(f"CodingSession {session_id} not found")
        return session

    async def _get_repository(
        self,
        db: AsyncSession,
        repo_id: UUID,
        user_id: UUID,
    ) -> Repository:
        """Load a Repository row or raise."""
        result = await db.execute(
            select(Repository).where(
                Repository.id == repo_id,
                Repository.user_id == user_id,
            )
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
        clone_url = _coerce_repository_clone_url(getattr(repo, "clone_url", None))
        if clone_url is None:
            raise ValueError(f"Repository {repo.id} has no clone_url")
        github_token = _coerce_github_clone_token(
            getattr(getattr(repo, "user", None), "github_token", None)
        )
        auth_clone_url = _build_authenticated_clone_url(
            clone_url,
            github_token,
        )

        # Clone into a temp directory
        import asyncio
        import shutil

        tmp_dir = Path(tempfile.mkdtemp(prefix="codey_repo_"))
        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--",
                    auth_clone_url,
                    str(tmp_dir / "repo"),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=_git_clone_env(),
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "git executable not found; install git to clone repositories"
                ) from exc
            except OSError as exc:
                raise RuntimeError(
                    f"failed to start git clone: {_clone_error_text(str(exc), '')}"
                ) from exc
            try:
                _stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=CLONE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                await _terminate_timed_out_clone(proc)
                raise RuntimeError(
                    f"git clone timed out after {CLONE_TIMEOUT_SECONDS}s"
                ) from exc

            if proc.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                stdout_text = (_stdout or b"").decode("utf-8", errors="replace").strip()
                raise RuntimeError(
                    "git clone failed "
                    f"(exit {proc.returncode}): "
                    f"{_clone_error_text(stderr_text, stdout_text)}"
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
        error_msg = _session_failure_error_text(exc)

        try:
            await db.rollback()
        except Exception as rollback_exc:
            logger.warning(
                "Failed to rollback failed session transaction for %s: %s",
                session_id,
                _session_failure_error_text(rollback_exc),
            )

        try:
            session = await self._get_session(db, session_id, user_id)
            session.status = "failed"
            session.error_message = error_msg
            session.completed_at = datetime.utcnow()

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
                _session_failure_error_text(inner),
            )

        await self._send(ws_session_id, {
            "type": "error",
            "message": error_msg,
        })

    async def _reserve_prompt_credits(
        self,
        credit_svc: CreditService,
        user_id: UUID,
        session_id: UUID,
        ws_session_id: str,
        charged: int,
        total_lines: int,
    ) -> int:
        """Reserve prompt-session credits without failing already-generated output."""
        if charged <= 0:
            return 0

        try:
            await credit_svc.reserve_credits(
                user_id,
                charged,
                f"Session {ws_session_id}: {total_lines} lines generated",
                session_id,
            )
            return charged
        except InsufficientCreditsError:
            balance = await credit_svc.get_balance(user_id)
            available = self._coerce_available_credits(balance)
            if available <= 0:
                return 0
            try:
                await credit_svc.reserve_credits(
                    user_id,
                    available,
                    f"Session {ws_session_id}: partial charge",
                    session_id,
                )
            except InsufficientCreditsError:
                logger.warning(
                    "Prompt session %s partial credit charge raced with another spend",
                    ws_session_id,
                )
                return 0
            return available

    @staticmethod
    def _coerce_available_credits(balance: object) -> int:
        if not isinstance(balance, dict):
            return 0
        value = balance.get("total")
        if isinstance(value, bool):
            return 0
        try:
            available = int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return available if available > 0 else 0

    @staticmethod
    def _coerce_generated_text(value: object) -> str:
        return value if isinstance(value, str) else ""

    @staticmethod
    def _coerce_generated_result(value: object) -> Mapping[str, object]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _generated_temp_suffix(file_path: str, language: str | None) -> str:
        ext_map = {
            "python": ".py",
            "javascript": ".js",
            "typescript": ".ts",
            "jsx": ".jsx",
            "tsx": ".tsx",
        }
        fallback = ext_map.get((language or "").lower(), ".py")
        try:
            suffix = Path(file_path).suffix
        except (TypeError, ValueError):
            return fallback
        if (
            not suffix
            or any(ord(char) < 32 or ord(char) == 127 for char in suffix)
        ):
            return fallback
        return suffix

    @staticmethod
    def _default_generated_file_path(language: str | None, index: int = 1) -> str:
        ext_map = {
            "python": ".py",
            "javascript": ".js",
            "typescript": ".ts",
            "jsx": ".jsx",
            "tsx": ".tsx",
        }
        ext = ext_map.get((language or "").lower(), ".py")
        stem = "generated" if index <= 1 else f"generated_{index}"
        return f"{stem}{ext}"

    @staticmethod
    def _unique_generated_file_path(
        language: str | None,
        existing: Mapping[str, str],
    ) -> str:
        index = 1
        while True:
            candidate = SessionRunner._default_generated_file_path(language, index)
            if candidate not in existing:
                return candidate
            index += 1

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
                file_path = normalize_plan_file_path(match.group(1))
                if file_path is None:
                    file_path = SessionRunner._unique_generated_file_path(language, files)
                start = match.end()
                end = markers[i + 1].start() if i + 1 < len(markers) else len(code)
                content = code[start:end].strip()
                if content:
                    existing = files.get(file_path)
                    files[file_path] = (
                        f"{existing}\n\n{content}" if existing else content
                    )
        else:
            # Single file output
            files[SessionRunner._default_generated_file_path(language)] = (
                code.strip() if code else ""
            )

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
