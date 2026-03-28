"""Build Engine — orchestrates the entire autonomous project generation pipeline."""

from __future__ import annotations

import io
import logging
import os
import tempfile
import zipfile
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.graph.engine import CodebaseGraph
from codey.nfet.sweep import NFETSweep, Phase
from codey.parser.extractor import LanguageParser
from codey.saas.build_mode.decomposer import TaskDecomposer, TaskNode
from codey.saas.build_mode.generator import BuildContext, FileGenerator, GeneratedFile
from codey.saas.build_mode.planner import ProjectPlanner
from codey.saas.build_mode.templates import TemplateLibrary
from codey.saas.build_mode.validator import FileValidator
from codey.saas.credits.service import CreditService
from codey.saas.models.build_checkpoint import BuildCheckpoint
from codey.saas.models.build_file import BuildFile
from codey.saas.models.build_project import BuildProject

logger = logging.getLogger(__name__)

# NFET intervention thresholds
_NFET_CAUTION_THRESHOLD = 0.5
_NFET_CRITICAL_THRESHOLD = 0.3


class BuildEngine:
    """Orchestrates the full Build Mode pipeline.

    Lifecycle:
    1. start_build() — plan the project, return plan for user approval
    2. approve_and_build() — execute the plan phase by phase, yield progress
    3. handle_checkpoint_action() — process user decisions at phase boundaries
    """

    def __init__(self, db: AsyncSession, user_id: UUID) -> None:
        self.db = db
        self.user_id = user_id

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.planner = ProjectPlanner(api_key=api_key)
        self.decomposer = TaskDecomposer()
        self.generator = FileGenerator(api_key=api_key)
        self.validator = FileValidator()
        self.templates = TemplateLibrary()
        self.credit_service = CreditService(db)
        self.nfet = NFETSweep()

    # ------------------------------------------------------------------
    # Step 1: Planning
    # ------------------------------------------------------------------

    async def start_build(self, description: str) -> dict[str, Any]:
        """Analyze the description, optionally clarify, and generate a plan.

        Returns a dict containing the plan and any clarification questions.
        The caller should present the plan for user approval before proceeding.
        """
        # Step 1a: Check if clarification is needed
        clarification = await self.planner.clarify(description)

        if clarification.get("questions"):
            return {
                "status": "needs_clarification",
                "questions": clarification["questions"],
                "defaults": clarification["defaults"],
                "template_match": clarification.get("template_match"),
            }

        # Step 1b: Generate the plan (may use a template)
        plan = await self.planner.create_plan(description)

        # Step 1c: Check credits
        estimated = plan.get("estimated_credits", {})
        min_credits = estimated.get("min", 10)
        has_credits = await self.credit_service.check_credits(
            self.user_id, min_credits
        )

        # Step 1d: Persist the project record
        project = BuildProject(
            user_id=self.user_id,
            name=plan.get("name", "Untitled"),
            description=plan.get("description", ""),
            status="planning",
            total_phases=len(plan.get("phases", [])),
            files_planned=len(plan.get("file_tree", {})),
            project_plan=plan,
            file_tree=plan.get("file_tree"),
            stack=plan.get("stack"),
        )
        self.db.add(project)
        await self.db.flush()

        return {
            "status": "plan_ready",
            "project_id": str(project.id),
            "plan": plan,
            "has_credits": has_credits,
            "estimated_credits": estimated,
        }

    async def start_build_with_answers(
        self,
        description: str,
        answers: dict[str, str],
    ) -> dict[str, Any]:
        """Generate plan after clarification questions have been answered."""
        plan = await self.planner.create_plan(description, answers)

        estimated = plan.get("estimated_credits", {})
        min_credits = estimated.get("min", 10)
        has_credits = await self.credit_service.check_credits(
            self.user_id, min_credits
        )

        project = BuildProject(
            user_id=self.user_id,
            name=plan.get("name", "Untitled"),
            description=plan.get("description", ""),
            status="planning",
            total_phases=len(plan.get("phases", [])),
            files_planned=len(plan.get("file_tree", {})),
            project_plan=plan,
            file_tree=plan.get("file_tree"),
            stack=plan.get("stack"),
        )
        self.db.add(project)
        await self.db.flush()

        return {
            "status": "plan_ready",
            "project_id": str(project.id),
            "plan": plan,
            "has_credits": has_credits,
            "estimated_credits": estimated,
        }

    # ------------------------------------------------------------------
    # Step 2: Build execution
    # ------------------------------------------------------------------

    async def approve_and_build(
        self,
        project_id: UUID,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute the approved build plan, yielding status updates.

        This is the main build loop. It:
        1. Loads the project and decomposes into tasks
        2. For each phase, generates files, validates, runs NFET
        3. At phase boundaries, creates checkpoints and yields pause points
        4. On completion, packages the project and yields the final result
        """
        # Load the project
        result = await self.db.execute(
            select(BuildProject).where(BuildProject.id == project_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            yield {"status": "error", "message": "Project not found"}
            return

        plan = project.project_plan
        if not plan:
            yield {"status": "error", "message": "Project has no plan"}
            return

        # Update status
        project.status = "building"
        await self.db.flush()

        yield {
            "status": "build_started",
            "project_id": str(project_id),
            "total_phases": project.total_phases,
            "total_files": project.files_planned,
        }

        # Decompose into tasks
        tasks = self.decomposer.decompose(plan)
        if not tasks:
            yield {"status": "error", "message": "Task decomposition produced no tasks"}
            return

        # Initialize build context
        context = BuildContext(project_plan=plan)

        # Initialize NFET graph for structural analysis
        graph = CodebaseGraph()

        # Group tasks by phase
        phase_tasks: dict[int, list[TaskNode]] = {}
        for task in tasks:
            phase_tasks.setdefault(task.phase, []).append(task)

        total_files_done = 0
        total_lines = 0
        total_credits = 0.0

        # Build phase by phase
        for phase_num in sorted(phase_tasks.keys()):
            phase_task_list = phase_tasks[phase_num]
            phase_info = self._get_phase_info(plan, phase_num)

            yield {
                "status": "phase_started",
                "phase": phase_num,
                "phase_name": phase_info.get("name", f"Phase {phase_num}"),
                "files_in_phase": len(phase_task_list),
            }

            project.current_phase = phase_num
            await self.db.flush()

            phase_files_done = 0

            for task in phase_task_list:
                yield {
                    "status": "generating_file",
                    "phase": phase_num,
                    "file_path": task.file_path,
                    "file_type": task.file_type,
                    "estimated_lines": task.estimated_lines,
                }

                # Generate the file
                generated: GeneratedFile | None = None
                attempts = 0
                max_attempts = 3
                last_error: str | None = None

                while attempts < max_attempts:
                    attempts += 1
                    try:
                        generated = await self.generator.generate_file(task, context)
                    except Exception as e:
                        last_error = str(e)
                        logger.warning(
                            "Generation attempt %d/%d failed for %s: %s",
                            attempts, max_attempts, task.file_path, e,
                        )
                        continue

                    # Validate syntax
                    passed, error = self.validator.validate_syntax(
                        generated.content, generated.path
                    )
                    if passed:
                        break
                    else:
                        last_error = error
                        logger.warning(
                            "Syntax validation failed for %s (attempt %d): %s",
                            task.file_path, attempts, error,
                        )
                        generated = None

                if generated is None:
                    yield {
                        "status": "file_failed",
                        "file_path": task.file_path,
                        "error": last_error or "Unknown error",
                        "attempts": attempts,
                    }
                    # Create a placeholder BuildFile record
                    build_file = BuildFile(
                        project_id=project_id,
                        file_path=task.file_path,
                        phase=phase_num,
                        status="failed",
                        generation_attempts=attempts,
                        validation_passed=False,
                    )
                    self.db.add(build_file)
                    await self.db.flush()
                    continue

                # File generated successfully
                file_credits = self._calculate_file_credits(generated.line_count)

                # Update context
                context.generated_files[task.file_path] = generated.content
                context.file_summaries[task.file_path] = generated.summary

                # Check import validation
                existing_files = set(context.generated_files.keys())
                import_errors = self.validator.validate_imports(
                    generated.content, generated.path, existing_files
                )

                # Update NFET graph with the new file
                nfet_result = self._update_nfet_graph(
                    graph, task.file_path, generated.content
                )
                if nfet_result:
                    context.nfet_state = {
                        "phase": nfet_result.phase.value,
                        "es": round(nfet_result.es_score, 4),
                        "kappa": round(nfet_result.kappa, 4),
                        "sigma": round(nfet_result.sigma, 4),
                    }

                # Persist the file
                stress = nfet_result.highest_stress_value if nfet_result else None
                build_file = BuildFile(
                    project_id=project_id,
                    file_path=task.file_path,
                    content=generated.content,
                    line_count=generated.line_count,
                    phase=phase_num,
                    status="completed",
                    stress_score=stress,
                    generation_attempts=attempts,
                    validation_passed=len(import_errors) == 0,
                    credits_charged=file_credits,
                    generated_at=datetime.utcnow(),
                )
                self.db.add(build_file)

                total_files_done += 1
                phase_files_done += 1
                total_lines += generated.line_count
                total_credits += file_credits

                # Update project stats
                project.files_completed = total_files_done
                project.lines_generated = total_lines
                project.credits_charged = int(total_credits)
                await self.db.flush()

                yield {
                    "status": "file_completed",
                    "file_path": task.file_path,
                    "line_count": generated.line_count,
                    "credits": file_credits,
                    "import_warnings": import_errors[:5],
                    "nfet_state": context.nfet_state,
                    "progress": {
                        "files_done": total_files_done,
                        "total_files": project.files_planned or len(tasks),
                        "phase_files_done": phase_files_done,
                        "phase_files_total": len(phase_task_list),
                    },
                }

                # Check NFET intervention
                intervention = self._check_nfet_intervention(
                    context.nfet_state, task.file_path
                )
                if intervention:
                    yield {
                        "status": "nfet_intervention",
                        "warning": intervention["message"],
                        "nfet_state": context.nfet_state,
                        "recommendation": intervention["recommendation"],
                    }

            # Phase complete — run phase validation
            phase_summary = (
                f"Phase {phase_num} ({phase_info.get('name', '')}): "
                f"{phase_files_done} files, {total_lines} total lines"
            )
            context.phase_summaries.append(phase_summary)

            # Create checkpoint
            nfet_state = context.nfet_state
            checkpoint = BuildCheckpoint(
                project_id=project_id,
                phase=phase_num,
                phase_name=phase_info.get("name", f"Phase {phase_num}"),
                files_in_phase=phase_files_done,
                tests_passed=0,
                tests_failed=0,
                nfet_es_score=nfet_state.get("es"),
                nfet_kappa=nfet_state.get("kappa"),
                nfet_sigma=nfet_state.get("sigma"),
            )
            self.db.add(checkpoint)
            await self.db.flush()

            yield {
                "status": "checkpoint",
                "phase": phase_num,
                "phase_name": phase_info.get("name", f"Phase {phase_num}"),
                "files_completed": phase_files_done,
                "nfet_state": context.nfet_state,
                "checkpoint_id": str(checkpoint.id),
                "total_progress": {
                    "files_done": total_files_done,
                    "total_files": project.files_planned or len(tasks),
                    "lines_generated": total_lines,
                    "credits_used": total_credits,
                },
            }

        # Build complete — charge credits and package
        try:
            credit_amount = max(1, int(total_credits))
            await self.credit_service.reserve_credits(
                self.user_id,
                credit_amount,
                f"Build Mode: {project.name} ({total_files_done} files)",
            )
        except Exception as e:
            logger.error("Credit charge failed for build %s: %s", project_id, e)

        # Package as zip
        download_url = await self._package_project(project_id, context)
        project.download_url = download_url
        project.status = "completed"
        project.completed_at = datetime.utcnow()

        # Final NFET scores
        nfet_final = context.nfet_state
        project.nfet_es_score_final = nfet_final.get("es")
        project.nfet_phase_final = nfet_final.get("phase")

        await self.db.flush()

        yield {
            "status": "build_complete",
            "project_id": str(project_id),
            "files_generated": total_files_done,
            "lines_generated": total_lines,
            "credits_charged": int(total_credits),
            "nfet_final": context.nfet_state,
            "download_url": download_url,
        }

    # ------------------------------------------------------------------
    # Step 3: Checkpoint handling
    # ------------------------------------------------------------------

    async def handle_checkpoint_action(
        self,
        project_id: UUID,
        phase: int,
        action: str,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Process the user's decision at a checkpoint.

        Actions:
        - "continue": Proceed to next phase
        - "review": Pause for manual review (user will resume later)
        - "modify": Accept modifications before continuing

        Returns status dict.
        """
        # Update the checkpoint record
        result = await self.db.execute(
            select(BuildCheckpoint).where(
                BuildCheckpoint.project_id == project_id,
                BuildCheckpoint.phase == phase,
            ).order_by(BuildCheckpoint.checkpoint_at.desc())
        )
        checkpoint = result.scalar_one_or_none()

        if checkpoint:
            checkpoint.user_action = action
            checkpoint.user_notes = notes

        if action == "continue":
            return {
                "status": "resuming",
                "next_phase": phase + 1,
                "message": "Continuing to next phase",
            }
        elif action == "review":
            # Mark project as paused
            proj_result = await self.db.execute(
                select(BuildProject).where(BuildProject.id == project_id)
            )
            project = proj_result.scalar_one_or_none()
            if project:
                project.status = "paused"
            await self.db.flush()

            return {
                "status": "paused",
                "message": "Build paused for review. Resume when ready.",
            }
        elif action == "modify":
            return {
                "status": "awaiting_modifications",
                "message": "Provide modifications and the build will adjust.",
                "notes": notes,
            }
        else:
            return {
                "status": "error",
                "message": f"Unknown action: {action}",
            }

    # ------------------------------------------------------------------
    # Credit calculation
    # ------------------------------------------------------------------

    def _calculate_file_credits(self, line_count: int) -> float:
        """Calculate credit cost for a generated file based on line count."""
        if line_count < 50:
            return 0.5
        elif line_count < 150:
            return 1.0
        elif line_count < 300:
            return 2.0
        elif line_count < 500:
            return 3.5
        else:
            return 5.0

    # ------------------------------------------------------------------
    # NFET integration
    # ------------------------------------------------------------------

    def _update_nfet_graph(
        self,
        graph: CodebaseGraph,
        file_path: str,
        content: str,
    ) -> Any:
        """Parse a generated file and update the NFET graph.

        Returns the SweepResult or None if parsing fails.
        """
        # Only parse Python and JS/TS files
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        if ext not in ("py", "js", "jsx", "ts", "tsx"):
            return None

        try:
            # Write to a temp file for the parser
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=f".{ext}",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(content)
                temp_path = f.name

            parser = LanguageParser()
            nodes, edges = parser.parse_file(Path(temp_path))

            # Remap file paths in nodes to use the logical project path
            for node in nodes:
                node.file_path = file_path

            graph.update_file(file_path, nodes, edges)

            # Run NFET sweep
            sweep_result = self.nfet.run(graph)
            return sweep_result

        except Exception as e:
            logger.warning("NFET update failed for %s: %s", file_path, e)
            return None
        finally:
            try:
                os.unlink(temp_path)
            except (OSError, UnboundLocalError):
                pass

    def _check_nfet_intervention(
        self,
        nfet_state: dict[str, Any],
        file_path: str,
    ) -> dict[str, str] | None:
        """Check if the current NFET state warrants an architecture intervention.

        Returns a warning dict or None if everything is healthy.
        """
        es = nfet_state.get("es", 1.0)
        phase = nfet_state.get("phase", "ridge")
        kappa = nfet_state.get("kappa", 0.0)
        sigma = nfet_state.get("sigma", 1.0)

        if es < _NFET_CRITICAL_THRESHOLD:
            return {
                "message": (
                    f"CRITICAL: Equilibrium score dropped to {es:.3f} after generating "
                    f"{file_path}. The codebase structure is degrading."
                ),
                "recommendation": (
                    "Consider breaking this file into smaller modules, reducing "
                    "coupling between components, or introducing an interface layer."
                ),
            }

        if es < _NFET_CAUTION_THRESHOLD:
            return {
                "message": (
                    f"CAUTION: Equilibrium score is {es:.3f} (kappa={kappa:.3f}, "
                    f"sigma={sigma:.3f}) after generating {file_path}."
                ),
                "recommendation": (
                    "Monitor coupling density. Consider extracting shared logic "
                    "into utility modules to improve structural balance."
                ),
            }

        if kappa > 0.8:
            return {
                "message": (
                    f"High coupling density detected (kappa={kappa:.3f}) after "
                    f"generating {file_path}."
                ),
                "recommendation": (
                    "Too many cross-module dependencies. Consider using dependency "
                    "injection or an event system to reduce direct coupling."
                ),
            }

        return None

    # ------------------------------------------------------------------
    # Project packaging
    # ------------------------------------------------------------------

    async def _package_project(
        self,
        project_id: UUID,
        context: BuildContext,
    ) -> str:
        """Package all generated files into a zip archive.

        Returns the path to the zip file (or a URL if uploaded to S3).
        """
        # Create zip in memory
        zip_buffer = io.BytesIO()
        project_name = context.project_plan.get("name", "project").replace(" ", "_").lower()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path, content in sorted(context.generated_files.items()):
                # Nest under project name directory
                archive_path = f"{project_name}/{file_path}"
                zf.writestr(archive_path, content)

        # Write to temp directory (in production, this would upload to S3)
        output_dir = Path(tempfile.gettempdir()) / "codey_builds"
        output_dir.mkdir(exist_ok=True)
        zip_path = output_dir / f"{project_name}_{project_id}.zip"

        zip_path.write_bytes(zip_buffer.getvalue())
        logger.info("Packaged build %s to %s (%d bytes)", project_id, zip_path, len(zip_buffer.getvalue()))

        return str(zip_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_phase_info(self, plan: dict[str, Any], phase_num: int) -> dict[str, Any]:
        """Get phase metadata from the plan."""
        phases = plan.get("phases", [])
        if 0 <= phase_num < len(phases):
            return phases[phase_num]
        return {"name": f"Phase {phase_num}", "description": "", "files": []}
