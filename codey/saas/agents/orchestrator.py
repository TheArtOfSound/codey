from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from codey.saas.intelligence.providers import call_model, resolve_model
from codey.saas.sandbox.manager import Sandbox, SandboxManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class AgentRole(str, Enum):
    ARCHITECT = "architect"
    BUILDER = "builder"
    TESTER = "tester"
    REVIEWER = "reviewer"
    DOCUMENTER = "documenter"
    DEBUGGER = "debugger"


class AgentStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class SubTask:
    id: str
    description: str
    role: AgentRole
    dependencies: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    context: str = ""
    priority: int = 0  # higher = earlier


@dataclass
class Agent:
    id: str
    role: AgentRole
    subtask: SubTask
    status: AgentStatus = AgentStatus.IDLE
    progress: float = 0.0
    output: str = ""
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class AgentResult:
    agent_id: str
    role: AgentRole
    success: bool
    output: str
    files_modified: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class FinalResult:
    success: bool
    summary: str
    results: list[AgentResult] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# System prompts per role
# ---------------------------------------------------------------------------

_ROLE_PROMPTS: dict[AgentRole, str] = {
    AgentRole.ARCHITECT: (
        "You are a software architect. Analyze the task and produce a clear, "
        "actionable implementation plan. Define file structure, interfaces, "
        "data flow, and key design decisions. Output a structured plan that "
        "other agents (builder, tester) can follow."
    ),
    AgentRole.BUILDER: (
        "You are a senior developer. Implement code based on the plan provided. "
        "Write clean, production-quality code with proper error handling, "
        "typing, and docstrings. Output complete file contents."
    ),
    AgentRole.TESTER: (
        "You are a QA engineer. Write comprehensive tests for the code provided. "
        "Cover happy paths, edge cases, and error conditions. Use the project's "
        "testing framework. Output complete test files."
    ),
    AgentRole.REVIEWER: (
        "You are a code reviewer. Analyze the code for bugs, security issues, "
        "performance problems, and style violations. Provide specific, "
        "actionable feedback with file paths and line numbers."
    ),
    AgentRole.DOCUMENTER: (
        "You are a technical writer. Generate clear documentation for the code. "
        "Include docstrings, inline comments for complex logic, a README section, "
        "and API documentation if applicable."
    ),
    AgentRole.DEBUGGER: (
        "You are a debugging specialist. Analyze error messages, stack traces, "
        "and code to identify the root cause. Provide a fix with explanation. "
        "Output the corrected code."
    ),
}

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class AgentOrchestrator:
    """Decomposes tasks, assigns agents, and coordinates parallel execution."""

    def __init__(self, sandbox_manager: SandboxManager | None = None) -> None:
        self._sandbox_mgr = sandbox_manager or SandboxManager()
        self._agents: dict[str, Agent] = {}

    # -----------------------------------------------------------------------
    # Task decomposition
    # -----------------------------------------------------------------------

    async def decompose_task(
        self, task: str, context: dict | None = None
    ) -> list[SubTask]:
        """Use an AI model to decompose *task* into ordered subtasks."""
        context = context or {}
        provider, model = resolve_model("architecture")

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a project planner. Decompose the user's task into "
                    "specific subtasks. For each subtask, specify:\n"
                    "- description: what needs to be done\n"
                    "- role: one of architect, builder, tester, reviewer, documenter, debugger\n"
                    "- files: which files will be created or modified\n"
                    "- dependencies: IDs of subtasks that must complete first\n\n"
                    "Output as a JSON array of objects with keys: "
                    "id, description, role, files, dependencies.\n"
                    "Use IDs like 'st-1', 'st-2', etc."
                ),
            },
            {"role": "user", "content": task},
        ]

        if context.get("codebase_summary"):
            messages.insert(
                1,
                {
                    "role": "user",
                    "content": f"Current codebase context:\n{context['codebase_summary']}",
                },
            )

        response = await call_model(
            provider, model, messages, temperature=0.2, max_tokens=4096
        )

        return self._parse_subtasks(response)

    def _parse_subtasks(self, response: str) -> list[SubTask]:
        """Parse the model's JSON response into SubTask objects."""
        import json
        import re

        # Extract JSON from markdown fences if present
        json_match = re.search(r"```(?:json)?\s*\n(.*?)```", response, re.DOTALL)
        raw = json_match.group(1) if json_match else response

        # Try to find array in the text
        bracket_start = raw.find("[")
        bracket_end = raw.rfind("]")
        if bracket_start >= 0 and bracket_end > bracket_start:
            raw = raw[bracket_start : bracket_end + 1]

        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse subtask JSON, creating single builder task")
            return [
                SubTask(
                    id="st-1",
                    description=response[:500],
                    role=AgentRole.BUILDER,
                )
            ]

        subtasks: list[SubTask] = []
        for i, item in enumerate(items):
            role_str = item.get("role", "builder").lower()
            try:
                role = AgentRole(role_str)
            except ValueError:
                role = AgentRole.BUILDER

            subtasks.append(
                SubTask(
                    id=item.get("id", f"st-{i + 1}"),
                    description=item.get("description", ""),
                    role=role,
                    files=item.get("files", []),
                    dependencies=item.get("dependencies", []),
                    priority=len(items) - i,  # earlier items = higher priority
                )
            )
        return subtasks

    # -----------------------------------------------------------------------
    # Agent assignment
    # -----------------------------------------------------------------------

    async def assign_agents(
        self, subtasks: list[SubTask], max_parallel: int = 4
    ) -> list[Agent]:
        """Create :class:`Agent` instances for each subtask."""
        agents: list[Agent] = []
        for subtask in subtasks:
            agent = Agent(
                id=uuid.uuid4().hex[:12],
                role=subtask.role,
                subtask=subtask,
            )
            self._agents[agent.id] = agent
            agents.append(agent)
        return agents

    # -----------------------------------------------------------------------
    # Parallel execution
    # -----------------------------------------------------------------------

    async def run_parallel(
        self,
        agents: list[Agent],
        sandbox: Sandbox,
        *,
        max_parallel: int = 4,
    ) -> list[AgentResult]:
        """Execute agents respecting dependencies, up to *max_parallel* at a time."""
        results: dict[str, AgentResult] = {}
        completed_subtask_ids: set[str] = set()
        remaining = list(agents)
        semaphore = asyncio.Semaphore(max_parallel)

        async def run_one(agent: Agent) -> AgentResult:
            async with semaphore:
                return await self._execute_agent(agent, sandbox, results)

        while remaining:
            # Find agents whose dependencies are satisfied
            ready: list[Agent] = []
            still_waiting: list[Agent] = []

            for agent in remaining:
                deps = set(agent.subtask.dependencies)
                if deps.issubset(completed_subtask_ids):
                    ready.append(agent)
                else:
                    still_waiting.append(agent)

            if not ready:
                if still_waiting:
                    # Deadlock or missing dependencies — force run remaining
                    logger.warning(
                        "Dependency deadlock detected, forcing %d agents",
                        len(still_waiting),
                    )
                    ready = still_waiting
                    still_waiting = []
                else:
                    break

            # Run ready agents in parallel
            batch_results = await asyncio.gather(
                *(run_one(a) for a in ready), return_exceptions=True
            )

            for agent, result in zip(ready, batch_results):
                if isinstance(result, BaseException):
                    logger.exception(
                        "Agent %s (%s) raised an exception",
                        agent.id,
                        agent.role.value,
                    )
                    result = AgentResult(
                        agent_id=agent.id,
                        role=agent.role,
                        success=False,
                        output="",
                        error=str(result),
                    )
                results[agent.id] = result
                completed_subtask_ids.add(agent.subtask.id)

            remaining = still_waiting

        return list(results.values())

    async def _execute_agent(
        self,
        agent: Agent,
        sandbox: Sandbox,
        prior_results: dict[str, AgentResult],
    ) -> AgentResult:
        """Run a single agent's subtask against a model."""
        agent.status = AgentStatus.WORKING
        agent.started_at = time.time()

        try:
            provider, model = resolve_model(self._model_key_for_role(agent.role))

            # Build context from prior results (dependency outputs)
            dep_context = ""
            for dep_id in agent.subtask.dependencies:
                # Find the agent whose subtask.id matches
                for aid, result in prior_results.items():
                    a = self._agents.get(aid)
                    if a and a.subtask.id == dep_id and result.success:
                        dep_context += (
                            f"\n--- Output from {a.role.value} (task {dep_id}) ---\n"
                            f"{result.output[:4000]}\n"
                        )

            # Read existing files referenced by the subtask
            file_context = ""
            sandbox_mgr = self._sandbox_mgr
            for fpath in agent.subtask.files:
                try:
                    content = await sandbox_mgr.read_file(sandbox.id, fpath)
                    file_context += f"\n--- {fpath} ---\n{content[:3000]}\n"
                except (FileNotFoundError, ValueError):
                    pass  # File doesn't exist yet

            messages = [
                {"role": "system", "content": _ROLE_PROMPTS[agent.role]},
                {
                    "role": "user",
                    "content": (
                        f"Task: {agent.subtask.description}\n\n"
                        f"Files involved: {', '.join(agent.subtask.files) or 'none specified'}\n\n"
                        f"{dep_context}"
                        f"{file_context}"
                    ),
                },
            ]

            output = await call_model(
                provider, model, messages, temperature=0.2, max_tokens=8192
            )

            # Write any generated files to the sandbox
            files_modified = await self._write_output_files(
                sandbox, output, agent.subtask.files
            )

            agent.status = AgentStatus.COMPLETE
            agent.progress = 1.0
            agent.output = output
            agent.finished_at = time.time()

            duration_ms = (agent.finished_at - agent.started_at) * 1000

            return AgentResult(
                agent_id=agent.id,
                role=agent.role,
                success=True,
                output=output,
                files_modified=files_modified,
                duration_ms=round(duration_ms, 1),
            )

        except Exception as exc:
            agent.status = AgentStatus.FAILED
            agent.error = str(exc)
            agent.finished_at = time.time()
            logger.exception("Agent %s failed", agent.id)
            return AgentResult(
                agent_id=agent.id,
                role=agent.role,
                success=False,
                output="",
                error=str(exc),
                duration_ms=(
                    (agent.finished_at - agent.started_at) * 1000
                    if agent.started_at
                    else 0
                ),
            )

    async def _write_output_files(
        self, sandbox: Sandbox, output: str, expected_files: list[str]
    ) -> list[str]:
        """Extract code blocks from model output and write them to the sandbox."""
        import re

        written: list[str] = []

        # Match patterns like "### filename.py" or "```python filename.py" followed by code
        # or fenced blocks with file path comments at the top
        file_pattern = re.compile(
            r"(?:#{1,4}\s+`?([^\n`]+\.\w+)`?\s*\n```\w*\n(.*?)```)"
            r"|(?:```\w*\s*\n#\s*([\w/.-]+\.\w+)\n(.*?)```)",
            re.DOTALL,
        )

        for m in file_pattern.finditer(output):
            filename = m.group(1) or m.group(3)
            content = m.group(2) or m.group(4)
            if filename and content:
                filename = filename.strip()
                try:
                    await self._sandbox_mgr.write_file(
                        sandbox.id, filename, content
                    )
                    written.append(filename)
                except (PermissionError, ValueError) as exc:
                    logger.warning("Could not write %s: %s", filename, exc)

        # If no files extracted but we have expected files and a single code block,
        # write the whole block to the first expected file
        if not written and expected_files:
            code_blocks = re.findall(r"```\w*\n(.*?)```", output, re.DOTALL)
            if code_blocks:
                try:
                    await self._sandbox_mgr.write_file(
                        sandbox.id, expected_files[0], code_blocks[0]
                    )
                    written.append(expected_files[0])
                except (PermissionError, ValueError) as exc:
                    logger.warning(
                        "Could not write %s: %s", expected_files[0], exc
                    )

        return written

    @staticmethod
    def _model_key_for_role(role: AgentRole) -> str:
        """Map an agent role to a model routing key."""
        mapping: dict[AgentRole, str] = {
            AgentRole.ARCHITECT: "architecture",
            AgentRole.BUILDER: "code_generation",
            AgentRole.TESTER: "test_generation",
            AgentRole.REVIEWER: "code_review",
            AgentRole.DOCUMENTER: "documentation",
            AgentRole.DEBUGGER: "debugging",
        }
        return mapping.get(role, "default")

    # -----------------------------------------------------------------------
    # Coordination
    # -----------------------------------------------------------------------

    async def coordinate(self, agents: list[Agent]) -> None:
        """Watch running agents for conflicts (e.g. same file edits)."""
        file_claims: dict[str, list[str]] = {}
        for agent in agents:
            for fpath in agent.subtask.files:
                file_claims.setdefault(fpath, []).append(agent.id)

        conflicts = {
            fpath: agent_ids
            for fpath, agent_ids in file_claims.items()
            if len(agent_ids) > 1
        }
        if conflicts:
            logger.warning("File conflicts detected: %s", conflicts)
            # Resolve by giving priority to the agent with the higher-priority subtask
            for fpath, agent_ids in conflicts.items():
                agents_sorted = sorted(
                    (self._agents[aid] for aid in agent_ids if aid in self._agents),
                    key=lambda a: a.subtask.priority,
                    reverse=True,
                )
                if len(agents_sorted) > 1:
                    # Remove the file from lower-priority agents
                    for lower_agent in agents_sorted[1:]:
                        if fpath in lower_agent.subtask.files:
                            lower_agent.subtask.files.remove(fpath)
                            logger.info(
                                "Removed %s from agent %s (lower priority)",
                                fpath,
                                lower_agent.id,
                            )

    # -----------------------------------------------------------------------
    # Merge
    # -----------------------------------------------------------------------

    async def merge_results(self, results: list[AgentResult]) -> FinalResult:
        """Combine all agent results into a :class:`FinalResult`."""
        all_files_modified: list[str] = []
        all_files_created: list[str] = []
        conflicts: list[str] = []
        summaries: list[str] = []

        seen_files: dict[str, str] = {}  # file -> agent_id
        for result in results:
            status = "OK" if result.success else "FAILED"
            summaries.append(
                f"[{result.role.value}] {status} ({result.duration_ms:.0f}ms)"
            )
            for fpath in result.files_modified:
                if fpath in seen_files:
                    conflicts.append(
                        f"{fpath} modified by both {seen_files[fpath]} and {result.agent_id}"
                    )
                else:
                    seen_files[fpath] = result.agent_id
                all_files_modified.append(fpath)

        # Deduplicate
        all_files_modified = list(dict.fromkeys(all_files_modified))

        success = all(r.success for r in results)
        summary = "\n".join(summaries)
        if conflicts:
            summary += "\n\nConflicts:\n" + "\n".join(conflicts)

        return FinalResult(
            success=success,
            summary=summary,
            results=results,
            files_created=all_files_created,
            files_modified=all_files_modified,
            conflicts=conflicts,
        )
