"""Project Planner — generates complete project plans via Claude API."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import anthropic

from codey.saas.build_mode.templates import TemplateLibrary

logger = logging.getLogger(__name__)

# Credit estimation per file type
_CREDITS_PER_FILE: dict[str, float] = {
    "config": 0.3,
    "model": 1.0,
    "schema": 0.8,
    "service": 2.0,
    "middleware": 1.0,
    "router": 1.5,
    "component": 1.5,
    "page": 1.5,
    "hook": 0.8,
    "util": 1.0,
    "test": 1.5,
    "docker": 0.3,
    "migration": 0.5,
    "static": 0.2,
    "style": 0.5,
}


class ProjectPlanner:
    """Generates complete project plans by analyzing user descriptions.

    The planner can:
    1. Identify ambiguities and ask clarifying questions
    2. Match the description to a known template for fast planning
    3. Generate a fully custom plan via Claude API
    4. Estimate credit costs based on file counts and types
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._templates = TemplateLibrary()

    async def clarify(self, user_description: str) -> dict[str, Any]:
        """Analyze the user's description and identify ambiguities.

        Returns a dict with:
        - questions: list of clarifying questions (max 5)
        - defaults: suggested default answers for each question
        - template_match: matched template key or None
        """
        # Check for template match first
        template_key = self._match_template(user_description)
        if template_key:
            template = self._templates.get_template(template_key)
            if template:
                return {
                    "questions": [],
                    "defaults": {},
                    "template_match": template_key,
                    "template_name": template["name"],
                    "template_description": template["description"],
                    "estimated_credits": template["estimated_credits"],
                }

        # Use Claude to analyze the description and generate questions
        from codey.saas.intelligence.providers import call_model, resolve_model
        provider, model = resolve_model('architecture')

        system = (
            "You are a project planning assistant. Analyze the user's project description "
            "and identify any ambiguities or missing details that would be needed to build it. "
            "Generate at most 5 clarifying questions, each with a sensible default answer.\n\n"
            "Return your response as JSON with this exact structure:\n"
            "{\n"
            '  "questions": ["question1", "question2", ...],\n'
            '  "defaults": {"question1": "default1", "question2": "default2", ...}\n'
            "}\n\n"
            "Focus on: tech stack preferences, authentication needs, database choice, "
            "key features to include/exclude, deployment target.\n"
            "Do NOT ask about things that are already clear from the description.\n"
            "If the description is already very detailed, return fewer or no questions."
        )

        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user_description}],
        )

        raw = raw_response

        parsed = self._extract_json(raw)

        questions = parsed.get("questions", [])[:5]
        defaults = parsed.get("defaults", {})

        return {
            "questions": questions,
            "defaults": defaults,
            "template_match": None,
        }

    async def create_plan(
        self,
        user_description: str,
        answers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Generate the complete project plan.

        If a template matches, uses the template directly.
        Otherwise, generates a custom plan via Claude API.

        Returns a plan dict with:
        - name, description, stack, file_tree, phases
        - estimated_credits, estimated_time_minutes, deliverables
        """
        # Check for template match
        template_key = self._match_template(user_description)
        if template_key:
            template = self._templates.get_template(template_key)
            if template:
                return self._plan_from_template(template, user_description)

        # Generate custom plan via Claude
        return await self._generate_custom_plan(user_description, answers)

    async def _generate_custom_plan(
        self,
        description: str,
        answers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Generate a fully custom project plan via Claude API."""
        from codey.saas.intelligence.providers import call_model, resolve_model
        provider, model = resolve_model('architecture')

        # Build context with any clarification answers
        context = f"Project Description:\n{description}"
        if answers:
            context += "\n\nClarification Answers:"
            for q, a in answers.items():
                context += f"\n- {q}: {a}"

        system = (
            "You are an expert software architect. Generate a complete project plan.\n\n"
            "Return your response as a single JSON object with this EXACT structure:\n"
            "{\n"
            '  "name": "Short Project Name",\n'
            '  "description": "One paragraph description of what this project does",\n'
            '  "stack": {\n'
            '    "frontend": "framework + version (or null if backend-only)",\n'
            '    "backend": "framework + version",\n'
            '    "database": "database + ORM",\n'
            '    "auth": "auth strategy (or null)",\n'
            '    "deployment": "deployment strategy"\n'
            "  },\n"
            '  "file_tree": {\n'
            '    "path/to/file.ext": "file_type",\n'
            '    "...": "..."\n'
            "  },\n"
            '  "phases": [\n'
            "    {\n"
            '      "name": "Phase Name",\n'
            '      "files": ["path/to/file1.ext", "path/to/file2.ext"],\n'
            '      "description": "What this phase accomplishes"\n'
            "    }\n"
            "  ],\n"
            '  "deliverables": ["list", "of", "key", "deliverables"]\n'
            "}\n\n"
            "File type must be one of: config, model, schema, service, middleware, "
            "router, component, page, hook, util, test, docker, migration, static, style\n\n"
            "RULES:\n"
            "- Generate a realistic file tree (20-60 files for most projects)\n"
            "- Organize phases so dependencies come before dependents\n"
            "- Config and models first, services next, then routers/API, then frontend, then tests\n"
            "- Each phase should have 3-8 files\n"
            "- Include test files for critical business logic\n"
            "- Include deployment files (Dockerfile, docker-compose)\n"
            "- Every file in phases must exist in file_tree and vice versa\n"
            "- Return ONLY the JSON object, no markdown, no explanation"
        )

        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8192,
            system=system,
            messages=[{"role": "user", "content": context}],
        )

        raw = raw_response

        plan = self._extract_json(raw)

        # Validate and enrich the plan
        plan = self._validate_plan(plan)
        plan["estimated_credits"] = self._estimate_credits(plan.get("file_tree", {}))
        plan["estimated_time_minutes"] = self._estimate_time(plan.get("file_tree", {}))

        if "deliverables" not in plan:
            plan["deliverables"] = self._infer_deliverables(plan)

        return plan

    def _plan_from_template(
        self,
        template: dict[str, Any],
        description: str,
    ) -> dict[str, Any]:
        """Create a plan from a matched template."""
        plan = {
            "name": template["name"],
            "description": description,
            "stack": template["stack"],
            "file_tree": template["file_tree"],
            "phases": template["phases"],
            "estimated_credits": template["estimated_credits"],
            "estimated_time_minutes": self._estimate_time(template["file_tree"]),
            "deliverables": self._infer_deliverables(template),
        }
        return plan

    def _validate_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Validate and fix common issues in generated plans."""
        # Ensure required keys exist
        plan.setdefault("name", "Untitled Project")
        plan.setdefault("description", "")
        plan.setdefault("stack", {})
        plan.setdefault("file_tree", {})
        plan.setdefault("phases", [])

        # Ensure every file in phases is in file_tree
        all_phase_files: set[str] = set()
        for phase in plan["phases"]:
            for fp in phase.get("files", []):
                all_phase_files.add(fp)
                if fp not in plan["file_tree"]:
                    plan["file_tree"][fp] = "service"  # default type

        # Ensure every file in file_tree is in some phase
        all_tree_files = set(plan["file_tree"].keys())
        unassigned = all_tree_files - all_phase_files
        if unassigned:
            # Add unassigned files to the last phase or create a new one
            if plan["phases"]:
                plan["phases"][-1]["files"].extend(sorted(unassigned))
            else:
                plan["phases"].append({
                    "name": "Main",
                    "files": sorted(unassigned),
                    "description": "All project files",
                })

        return plan

    def _estimate_credits(self, file_tree: dict[str, str]) -> dict[str, Any]:
        """Calculate credit cost range from the file tree."""
        if not file_tree:
            return {"min": 0, "max": 0, "breakdown": {}}

        total_min = 0.0
        total_max = 0.0
        breakdown: dict[str, dict[str, float]] = {}

        type_counts: dict[str, int] = {}
        for file_path, file_type in file_tree.items():
            ft = file_type.lower() if file_type else "service"
            type_counts[ft] = type_counts.get(ft, 0) + 1

        for ft, count in type_counts.items():
            per_file = _CREDITS_PER_FILE.get(ft, 1.0)
            min_cost = per_file * count * 0.7  # optimistic
            max_cost = per_file * count * 1.4  # pessimistic (retries, complexity)
            total_min += min_cost
            total_max += max_cost
            breakdown[ft] = {"count": count, "min": round(min_cost, 1), "max": round(max_cost, 1)}

        return {
            "min": max(1, int(total_min)),
            "max": int(total_max) + 1,
            "breakdown": breakdown,
        }

    def _estimate_time(self, file_tree: dict[str, str]) -> int:
        """Estimate build time in minutes based on file count."""
        count = len(file_tree)
        if count == 0:
            return 0
        # ~20 seconds per file (API call + validation) + 2 min overhead per phase
        # Assume ~6 files per phase
        estimated_phases = max(1, count // 6)
        return max(1, int((count * 20 / 60) + (estimated_phases * 2)))

    def _infer_deliverables(self, plan: dict[str, Any]) -> list[str]:
        """Infer deliverables from the plan structure."""
        deliverables: list[str] = []
        stack = plan.get("stack", {})
        file_tree = plan.get("file_tree", {})
        paths = list(file_tree.keys()) if isinstance(file_tree, dict) else []

        if stack.get("backend"):
            deliverables.append(f"Backend API ({stack['backend']})")
        if stack.get("frontend"):
            deliverables.append(f"Frontend application ({stack['frontend']})")
        if stack.get("database"):
            deliverables.append(f"Database layer ({stack['database']})")

        has_tests = any("test" in p.lower() for p in paths)
        if has_tests:
            deliverables.append("Test suite")

        has_docker = any("docker" in p.lower() for p in paths)
        if has_docker:
            deliverables.append("Docker deployment configuration")

        if not deliverables:
            deliverables.append("Complete project source code")
            deliverables.append("Project documentation")

        return deliverables

    def _match_template(self, description: str) -> str | None:
        """Match a description to a template."""
        return self._templates.match_template(description)

    def _extract_json(self, text: str) -> dict[str, Any]:
        """Extract a JSON object from LLM response text.

        Handles:
        - Pure JSON
        - JSON in code blocks
        - JSON with surrounding prose
        """
        # Try direct parse
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from code block
        code_block = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try finding JSON object in text
        # Find the first { and last } to extract embedded JSON
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            candidate = text[first_brace : last_brace + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        logger.error("Failed to extract JSON from response: %s...", text[:200])
        return {}
