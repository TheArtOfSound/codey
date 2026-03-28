"""File Generation Engine — produces source files via Claude API with full context management."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import anthropic

from codey.saas.build_mode.decomposer import TaskNode

logger = logging.getLogger(__name__)

# Maximum tokens to allocate for context (leave headroom for response)
_MAX_CONTEXT_TOKENS = 150_000
# Rough chars-per-token estimate for context budgeting
_CHARS_PER_TOKEN = 3.5
_MAX_CONTEXT_CHARS = int(_MAX_CONTEXT_TOKENS * _CHARS_PER_TOKEN)

# Maximum file summaries to include in full
_MAX_FULL_SUMMARIES = 40
# Maximum dependency files to include in full content
_MAX_FULL_DEPS = 8


@dataclass
class FileSummary:
    """Compact summary of a generated file for context passing."""

    file_path: str
    description: str
    exports: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    line_count: int = 0


@dataclass
class BuildContext:
    """Accumulated state during a build session."""

    project_plan: dict[str, Any]
    generated_files: dict[str, str] = field(default_factory=dict)  # path -> content
    file_summaries: dict[str, FileSummary] = field(default_factory=dict)  # path -> summary
    phase_summaries: list[str] = field(default_factory=list)
    nfet_state: dict[str, Any] = field(default_factory=lambda: {
        "phase": "ridge",
        "es": 1.0,
        "kappa": 0.0,
        "sigma": 1.0,
    })


@dataclass
class GeneratedFile:
    """Result of a file generation attempt."""

    path: str
    content: str
    summary: FileSummary
    line_count: int


class FileGenerator:
    """Generates individual source files using Claude API with structured context.

    Context management strategy:
    - Always include: system prompt, project plan, file being built
    - Include full content of direct dependencies (up to _MAX_FULL_DEPS)
    - Include summaries of all other generated files
    - Include NFET state for architecture awareness
    - Budget total context to stay within token limits
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key  # Kept for backward compat, but we use providers now

    async def generate_file(
        self,
        task: TaskNode,
        context: BuildContext,
    ) -> GeneratedFile:
        """Generate a single source file given the task and build context.

        Steps:
        1. Build the structured prompt with project plan, summaries, deps, NFET state
        2. Call LLM via provider system (Groq/OpenRouter/etc)
        3. Parse the response to extract clean file content
        4. Create a summary of the generated file
        5. Return GeneratedFile with content and metadata
        """
        from codey.saas.intelligence.providers import call_model, resolve_model

        system_prompt, messages = self._build_generation_prompt(task, context)

        provider, model = resolve_model("code_generation")
        api_messages = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]
        raw_response = await call_model(
            provider, model, api_messages, max_tokens=8192, temperature=0.2
        )

        content = self._parse_file_content(raw_response, task.file_path)
        line_count = content.count("\n") + 1 if content.strip() else 0
        summary = self._create_summary(task.file_path, content)

        return GeneratedFile(
            path=task.file_path,
            content=content,
            summary=summary,
            line_count=line_count,
        )

    def _build_generation_prompt(
        self,
        task: TaskNode,
        context: BuildContext,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Build the structured prompt for file generation.

        Returns (system_prompt, messages) for the Claude API call.
        """
        plan = context.project_plan
        stack = plan.get("stack", {})
        stack_desc = ", ".join(f"{k}: {v}" for k, v in stack.items()) if stack else "not specified"

        # System prompt
        system_prompt = (
            f"You are an expert software engineer building a {stack_desc} application.\n"
            "You generate production-quality source code. Follow these rules:\n"
            "- Write complete, working code — no placeholders, no TODOs, no stubs\n"
            "- Include all necessary imports\n"
            "- Follow the conventions of the stack (naming, file structure, patterns)\n"
            "- Handle errors properly\n"
            "- Add concise docstrings/comments for non-obvious logic\n"
            "- Match the style and patterns of the already-built files\n"
            "- Return ONLY the file content inside a single code block\n"
        )

        # Build the user message with sections
        sections: list[str] = []

        # Section 1: Project plan
        plan_summary = self._format_plan_summary(plan)
        sections.append(f"## PROJECT PLAN\n\n{plan_summary}")

        # Section 2: Already-built file summaries
        summaries_text = self._format_file_summaries(context.file_summaries, task)
        if summaries_text:
            sections.append(f"## ALREADY BUILT (summaries)\n\n{summaries_text}")

        # Section 3: Full content of direct dependencies
        deps_text = self._format_dependency_content(task, context)
        if deps_text:
            sections.append(f"## FULL CONTENT OF DIRECT DEPENDENCIES\n\n{deps_text}")

        # Section 4: NFET state
        nfet = context.nfet_state
        nfet_text = (
            f"Phase: {nfet.get('phase', 'unknown')}\n"
            f"Equilibrium Score (ES): {nfet.get('es', 'N/A')}\n"
            f"Coupling Density (kappa): {nfet.get('kappa', 'N/A')}\n"
            f"Cascade Margin (sigma): {nfet.get('sigma', 'N/A')}\n"
        )
        sections.append(f"## NFET STATE\n\n{nfet_text}")

        # Section 5: The file to build
        file_type = task.file_type
        estimated_lines = task.estimated_lines
        build_instruction = (
            f"## NOW BUILD: `{task.file_path}`\n\n"
            f"File type: {file_type}\n"
            f"Phase: {task.phase}\n"
            f"Estimated lines: ~{estimated_lines}\n"
        )
        if task.dependencies:
            build_instruction += f"Dependencies: {', '.join(task.dependencies)}\n"

        # Add phase-specific context from the plan
        phase_desc = self._get_phase_description(plan, task.phase)
        if phase_desc:
            build_instruction += f"\nPhase description: {phase_desc}\n"

        build_instruction += (
            "\nGenerate the complete file content. "
            "Wrap the output in a single code block with the appropriate language tag."
        )
        sections.append(build_instruction)

        user_content = "\n\n---\n\n".join(sections)

        # Budget check: trim summaries if over budget
        total_chars = len(system_prompt) + len(user_content)
        if total_chars > _MAX_CONTEXT_CHARS:
            logger.warning(
                "Context for %s is %d chars (budget %d) — trimming summaries",
                task.file_path,
                total_chars,
                _MAX_CONTEXT_CHARS,
            )
            # Rebuild with fewer summaries
            trimmed_summaries = self._format_file_summaries(
                context.file_summaries, task, max_count=15
            )
            sections[1] = f"## ALREADY BUILT (summaries)\n\n{trimmed_summaries}"
            user_content = "\n\n---\n\n".join(sections)

        messages = [{"role": "user", "content": user_content}]
        return system_prompt, messages

    def _format_plan_summary(self, plan: dict[str, Any]) -> str:
        """Format the project plan for inclusion in the prompt."""
        lines: list[str] = []
        lines.append(f"**Name:** {plan.get('name', 'Unnamed Project')}")
        lines.append(f"**Description:** {plan.get('description', 'No description')}")

        stack = plan.get("stack", {})
        if stack:
            stack_lines = [f"  - {k}: {v}" for k, v in stack.items()]
            lines.append("**Stack:**\n" + "\n".join(stack_lines))

        # File tree (compact)
        file_tree = plan.get("file_tree", {})
        if file_tree:
            tree_lines = [f"  {fp} ({ft})" for fp, ft in sorted(file_tree.items())]
            lines.append("**File Tree:**\n" + "\n".join(tree_lines))

        # Phases summary
        phases = plan.get("phases", [])
        if phases:
            phase_lines = []
            for i, phase in enumerate(phases):
                name = phase.get("name", f"Phase {i}")
                desc = phase.get("description", "")
                file_count = len(phase.get("files", []))
                phase_lines.append(f"  Phase {i} — {name}: {desc} ({file_count} files)")
            lines.append("**Phases:**\n" + "\n".join(phase_lines))

        return "\n".join(lines)

    def _format_file_summaries(
        self,
        summaries: dict[str, FileSummary],
        current_task: TaskNode,
        max_count: int = _MAX_FULL_SUMMARIES,
    ) -> str:
        """Format file summaries for context. Prioritizes dependencies and same-phase files."""
        if not summaries:
            return ""

        # Prioritize: direct deps first, then same phase, then everything else
        dep_set = set(current_task.dependencies)
        dep_summaries: list[FileSummary] = []
        same_phase: list[FileSummary] = []
        other: list[FileSummary] = []

        for path, summary in summaries.items():
            if path in dep_set:
                dep_summaries.append(summary)
            elif summary.file_path in dep_set:
                dep_summaries.append(summary)
            else:
                other.append(summary)

        ordered = dep_summaries + same_phase + other
        ordered = ordered[:max_count]

        lines: list[str] = []
        for s in ordered:
            entry = f"### `{s.file_path}` ({s.line_count} lines)\n"
            entry += f"{s.description}\n"
            if s.exports:
                entry += f"Exports: {', '.join(s.exports[:15])}\n"
            if s.imports:
                entry += f"Imports: {', '.join(s.imports[:10])}\n"
            if s.endpoints:
                entry += f"Endpoints: {', '.join(s.endpoints[:10])}\n"
            lines.append(entry)

        return "\n".join(lines)

    def _format_dependency_content(
        self,
        task: TaskNode,
        context: BuildContext,
    ) -> str:
        """Include full source content for direct dependencies."""
        if not task.dependencies:
            return ""

        sections: list[str] = []
        included = 0
        total_chars = 0

        for dep_path in task.dependencies:
            if included >= _MAX_FULL_DEPS:
                break
            content = context.generated_files.get(dep_path)
            if content is None:
                continue
            # Budget: don't exceed ~50% of context for deps alone
            if total_chars + len(content) > _MAX_CONTEXT_CHARS // 2:
                logger.info(
                    "Skipping full content of %s (context budget)",
                    dep_path,
                )
                continue
            sections.append(f"### `{dep_path}`\n```\n{content}\n```")
            total_chars += len(content)
            included += 1

        return "\n\n".join(sections)

    def _get_phase_description(self, plan: dict[str, Any], phase: int) -> str:
        """Get the description for a specific phase from the plan."""
        phases = plan.get("phases", [])
        if 0 <= phase < len(phases):
            return phases[phase].get("description", "")
        return ""

    def _parse_file_content(self, response: str, file_path: str) -> str:
        """Extract clean source code from the LLM response.

        Handles:
        - Code blocks with language tags (```python ... ```)
        - Code blocks without tags (``` ... ```)
        - Raw code (no code blocks — take everything)
        """
        # Try to extract from fenced code block
        # Match ```<optional-lang>\n...\n```
        pattern = r"```(?:\w+)?\s*\n(.*?)```"
        matches = re.findall(pattern, response, re.DOTALL)

        if matches:
            # If multiple code blocks, take the longest one
            # (the main file content is usually the longest)
            content = max(matches, key=len)
            return content.rstrip()

        # No code blocks — try to find code-like content
        # Strip any leading/trailing prose
        lines = response.strip().split("\n")
        code_lines: list[str] = []
        in_code = False

        for line in lines:
            stripped = line.strip()
            # Heuristic: lines that look like code
            if (
                stripped.startswith(("import ", "from ", "class ", "def ", "const ",
                                     "let ", "var ", "export ", "function ", "#",
                                     "//", "/*", "package ", "use ", "@",
                                     "{", "}", "<", "return ", "if ", "for "))
                or stripped.endswith(("{", "}", ";", ":", ",", ")"))
                or not stripped  # blank lines
                or in_code
            ):
                code_lines.append(line)
                in_code = True
            elif in_code and stripped:
                # Continuation of code
                code_lines.append(line)

        if code_lines:
            return "\n".join(code_lines).rstrip()

        # Last resort: return the whole response stripped
        return response.strip()

    def _create_summary(self, file_path: str, content: str) -> FileSummary:
        """Analyze generated file content and create a compact summary."""
        lines = content.split("\n")
        line_count = len(lines)

        exports: list[str] = []
        imports: list[str] = []
        endpoints: list[str] = []
        description_parts: list[str] = []

        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""

        if ext == "py":
            self._summarize_python(lines, exports, imports, endpoints, description_parts)
        elif ext in ("ts", "tsx", "js", "jsx"):
            self._summarize_javascript(lines, exports, imports, endpoints, description_parts)
        else:
            description_parts.append(f"{ext.upper()} file with {line_count} lines")

        description = "; ".join(description_parts) if description_parts else f"File: {file_path}"

        return FileSummary(
            file_path=file_path,
            description=description,
            exports=exports,
            imports=imports,
            endpoints=endpoints,
            line_count=line_count,
        )

    def _summarize_python(
        self,
        lines: list[str],
        exports: list[str],
        imports: list[str],
        endpoints: list[str],
        description_parts: list[str],
    ) -> None:
        """Extract summary info from Python source lines."""
        classes: list[str] = []
        functions: list[str] = []

        for line in lines:
            stripped = line.strip()

            # Imports
            if stripped.startswith("from ") and " import " in stripped:
                module = stripped.split("from ", 1)[1].split(" import")[0].strip()
                imports.append(module)
            elif stripped.startswith("import "):
                module = stripped.split("import ", 1)[1].split(" as")[0].split(",")[0].strip()
                imports.append(module)

            # Classes
            if stripped.startswith("class ") and ":" in stripped:
                class_name = stripped.split("class ", 1)[1].split("(")[0].split(":")[0].strip()
                classes.append(class_name)
                exports.append(class_name)

            # Functions
            if stripped.startswith("def ") and not stripped.startswith("def _"):
                func_name = stripped.split("def ", 1)[1].split("(")[0].strip()
                functions.append(func_name)
                exports.append(func_name)

            # FastAPI endpoints
            if stripped.startswith("@router.") or stripped.startswith("@app."):
                parts = stripped.split("(")
                if len(parts) >= 2:
                    route_info = parts[1].split(")")[0].strip().strip("'\"")
                    method = stripped.split(".")[1].split("(")[0] if "." in stripped else "get"
                    endpoints.append(f"{method.upper()} {route_info}")

        if classes:
            description_parts.append(f"Classes: {', '.join(classes)}")
        if functions:
            description_parts.append(f"Functions: {', '.join(functions[:8])}")
        if endpoints:
            description_parts.append(f"Endpoints: {', '.join(endpoints[:5])}")

    def _summarize_javascript(
        self,
        lines: list[str],
        exports: list[str],
        imports: list[str],
        endpoints: list[str],
        description_parts: list[str],
    ) -> None:
        """Extract summary info from JavaScript/TypeScript source lines."""
        components: list[str] = []
        functions: list[str] = []

        for line in lines:
            stripped = line.strip()

            # Imports
            if stripped.startswith("import ") and " from " in stripped:
                module = stripped.split(" from ")[-1].strip().strip("'\"").rstrip(";")
                imports.append(module)

            # Exports
            if "export default" in stripped:
                # export default function Foo / export default class Foo
                parts = stripped.split("export default ")[-1].split(" ")
                if len(parts) >= 2:
                    name = parts[1].split("(")[0].split("{")[0].split("<")[0].strip()
                    if name:
                        exports.append(name)
            elif stripped.startswith("export "):
                # export function foo / export const foo / export class Foo
                if "function " in stripped:
                    name = stripped.split("function ")[-1].split("(")[0].strip()
                    functions.append(name)
                    exports.append(name)
                elif "const " in stripped:
                    name = stripped.split("const ")[-1].split(" ")[0].split(":")[0].split("=")[0].strip()
                    exports.append(name)
                elif "class " in stripped:
                    name = stripped.split("class ")[-1].split(" ")[0].split("{")[0].split("<")[0].strip()
                    exports.append(name)

            # React components (const Foo = () => / function Foo()
            if re.match(r"^(export\s+)?(default\s+)?function\s+[A-Z]", stripped):
                name = stripped.split("function ")[-1].split("(")[0].strip()
                components.append(name)

            # API routes (Next.js patterns)
            if stripped.startswith("export async function") and any(
                m in stripped for m in ("GET", "POST", "PUT", "DELETE", "PATCH")
            ):
                method = stripped.split("function ")[-1].split("(")[0].strip()
                endpoints.append(method)

        if components:
            description_parts.append(f"Components: {', '.join(components)}")
        if functions:
            description_parts.append(f"Functions: {', '.join(functions[:8])}")
        if endpoints:
            description_parts.append(f"API handlers: {', '.join(endpoints)}")
