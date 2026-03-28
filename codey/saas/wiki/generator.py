from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codey.saas.intelligence.providers import call_model, resolve_model

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class WikiSection:
    title: str
    content: str
    subsections: list[WikiSection] = field(default_factory=list)


@dataclass
class WikiContent:
    project_name: str
    sections: list[WikiSection] = field(default_factory=list)
    raw_markdown: str = ""


@dataclass
class WikiDiff:
    added_sections: list[str] = field(default_factory=list)
    modified_sections: list[str] = field(default_factory=list)
    removed_sections: list[str] = field(default_factory=list)
    raw_diff: str = ""


@dataclass
class WikiResult:
    section_title: str
    content: str
    relevance: float = 0.0


# ---------------------------------------------------------------------------
# File extensions by category
# ---------------------------------------------------------------------------

_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".swift", ".kt",
}
_CONFIG_EXTENSIONS = {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
    ".env.example",
}
_DOC_EXTENSIONS = {".md", ".rst", ".txt"}

_IGNORE_DIRS = {
    "__pycache__", "node_modules", ".git", ".venv", "venv", "dist",
    "build", ".tox", ".mypy_cache", ".pytest_cache", "egg-info",
    ".next", ".nuxt",
}

MAX_FILE_SIZE = 100_000  # 100 KB — skip larger files
MAX_FILES_TO_PARSE = 200


# ---------------------------------------------------------------------------
# Project Wiki
# ---------------------------------------------------------------------------


class ProjectWiki:
    """Auto-generates and maintains project documentation from source code."""

    # -----------------------------------------------------------------------
    # Generate full wiki
    # -----------------------------------------------------------------------

    async def generate(
        self, project_path: str, context: dict | None = None
    ) -> WikiContent:
        """Parse the codebase at *project_path* and generate wiki content.

        Produces sections for:
        - Architecture overview
        - API reference (from route decorators)
        - Database schema (from model definitions)
        - Setup guide
        - Environment variables
        """
        context = context or {}
        root = Path(project_path)
        project_name = context.get("project_name", root.name)

        # Gather codebase info
        file_tree = self._build_file_tree(root)
        code_summary = self._extract_code_summary(root)
        routes = self._extract_routes(root)
        models = self._extract_models(root)
        env_vars = self._extract_env_vars(root)
        dependencies = self._extract_dependencies(root)

        # Build sections
        sections: list[WikiSection] = []

        # 1. Architecture overview (AI-generated)
        arch_section = await self._generate_architecture_section(
            project_name, file_tree, code_summary, context
        )
        sections.append(arch_section)

        # 2. API Reference
        if routes:
            sections.append(self._build_api_section(routes))

        # 3. Database Schema
        if models:
            sections.append(self._build_schema_section(models))

        # 4. Setup Guide
        sections.append(
            self._build_setup_section(project_name, dependencies, env_vars)
        )

        # 5. Environment Variables
        if env_vars:
            sections.append(self._build_env_section(env_vars))

        # Render to markdown
        raw_md = self._render_markdown(project_name, sections)

        return WikiContent(
            project_name=project_name,
            sections=sections,
            raw_markdown=raw_md,
        )

    # -----------------------------------------------------------------------
    # Update wiki from changes
    # -----------------------------------------------------------------------

    async def update(
        self, project_path: str, changes: list[str], context: dict | None = None
    ) -> WikiDiff:
        """Incrementally update the wiki given a list of changed file paths."""
        context = context or {}
        root = Path(project_path)

        # Categorize changes
        route_changes: list[str] = []
        model_changes: list[str] = []
        config_changes: list[str] = []
        code_changes: list[str] = []

        for change in changes:
            p = Path(change)
            ext = p.suffix
            name = p.name.lower()
            content = ""
            full_path = root / change
            if full_path.exists() and full_path.stat().st_size < MAX_FILE_SIZE:
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

            if "route" in name or "router" in name or "@app." in content or "@router." in content:
                route_changes.append(change)
            elif "model" in name or "schema" in name:
                model_changes.append(change)
            elif ext in _CONFIG_EXTENSIONS:
                config_changes.append(change)
            else:
                code_changes.append(change)

        added: list[str] = []
        modified: list[str] = []

        if route_changes:
            modified.append("API Reference")
        if model_changes:
            modified.append("Database Schema")
        if config_changes:
            modified.append("Environment Variables")
        if code_changes:
            modified.append("Architecture Overview")

        # Generate a diff summary using AI
        provider, model = resolve_model("documentation")
        diff_prompt = (
            f"These files changed in the project:\n"
            + "\n".join(f"- {c}" for c in changes[:30])
            + "\n\nSummarize what documentation sections need updating and why."
        )
        diff_summary = await call_model(
            provider,
            model,
            [{"role": "user", "content": diff_prompt}],
            temperature=0.3,
            max_tokens=1024,
        )

        return WikiDiff(
            added_sections=added,
            modified_sections=modified,
            raw_diff=diff_summary,
        )

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    async def search(
        self, project_id: str, query: str, wiki_content: WikiContent | None = None
    ) -> list[WikiResult]:
        """Search wiki sections for *query* using keyword matching.

        For a production system this would use embeddings; here we use
        basic TF scoring against section content.
        """
        if wiki_content is None:
            return []

        query_terms = set(query.lower().split())
        results: list[WikiResult] = []

        for section in wiki_content.sections:
            score = self._score_section(section, query_terms)
            if score > 0:
                results.append(
                    WikiResult(
                        section_title=section.title,
                        content=section.content[:500],
                        relevance=score,
                    )
                )
            for sub in section.subsections:
                sub_score = self._score_section(sub, query_terms)
                if sub_score > 0:
                    results.append(
                        WikiResult(
                            section_title=f"{section.title} > {sub.title}",
                            content=sub.content[:500],
                            relevance=sub_score,
                        )
                    )

        results.sort(key=lambda r: r.relevance, reverse=True)
        return results[:10]

    @staticmethod
    def _score_section(section: WikiSection, query_terms: set[str]) -> float:
        """Simple keyword relevance score."""
        text = (section.title + " " + section.content).lower()
        hits = sum(1 for term in query_terms if term in text)
        return hits / max(len(query_terms), 1)

    # -----------------------------------------------------------------------
    # Codebase parsing
    # -----------------------------------------------------------------------

    def _build_file_tree(self, root: Path) -> str:
        """Build an indented file tree string."""
        lines: list[str] = []
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored directories
            dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
            rel = Path(dirpath).relative_to(root)
            depth = len(rel.parts)
            indent = "  " * depth
            if depth > 0:
                lines.append(f"{indent}{rel.name}/")
            for fname in sorted(filenames):
                if count >= MAX_FILES_TO_PARSE:
                    lines.append(f"{indent}  ... (truncated)")
                    return "\n".join(lines)
                lines.append(f"{indent}  {fname}")
                count += 1
        return "\n".join(lines)

    def _extract_code_summary(self, root: Path) -> str:
        """Extract top-level classes, functions, and imports from Python files."""
        summaries: list[str] = []
        count = 0
        for pyfile in root.rglob("*.py"):
            if count >= MAX_FILES_TO_PARSE:
                break
            if any(part in _IGNORE_DIRS for part in pyfile.parts):
                continue
            if pyfile.stat().st_size > MAX_FILE_SIZE:
                continue

            try:
                content = pyfile.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            rel = pyfile.relative_to(root)
            classes = re.findall(r"^class\s+(\w+)", content, re.MULTILINE)
            functions = re.findall(r"^def\s+(\w+)", content, re.MULTILINE)
            if classes or functions:
                parts = [str(rel)]
                if classes:
                    parts.append(f"  classes: {', '.join(classes)}")
                if functions:
                    parts.append(f"  functions: {', '.join(functions[:10])}")
                summaries.append("\n".join(parts))
            count += 1

        return "\n\n".join(summaries[:50])

    def _extract_routes(self, root: Path) -> list[dict[str, str]]:
        """Find API route decorators in the codebase."""
        routes: list[dict[str, str]] = []
        route_pattern = re.compile(
            r'@(?:app|router|blueprint)\.'
            r'(get|post|put|patch|delete|head|options)'
            r'\(\s*["\']([^"\']+)["\']'
            r'(?:.*?response_model\s*=\s*(\w+))?',
            re.IGNORECASE | re.DOTALL,
        )

        for pyfile in root.rglob("*.py"):
            if any(part in _IGNORE_DIRS for part in pyfile.parts):
                continue
            if pyfile.stat().st_size > MAX_FILE_SIZE:
                continue
            try:
                content = pyfile.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            for match in route_pattern.finditer(content):
                method = match.group(1).upper()
                path = match.group(2)
                response_model = match.group(3) or ""

                # Try to find the function name and docstring
                func_match = re.search(
                    r"(?:async\s+)?def\s+(\w+)\s*\(.*?\).*?:\s*(?:\n\s+\"\"\"(.*?)\"\"\")?",
                    content[match.end():match.end() + 500],
                    re.DOTALL,
                )
                func_name = func_match.group(1) if func_match else ""
                docstring = func_match.group(2).strip() if func_match and func_match.group(2) else ""

                routes.append({
                    "method": method,
                    "path": path,
                    "function": func_name,
                    "description": docstring[:200],
                    "response_model": response_model,
                    "file": str(pyfile.relative_to(root)),
                })

        return routes

    def _extract_models(self, root: Path) -> list[dict[str, Any]]:
        """Extract SQLAlchemy or Django model definitions."""
        models_found: list[dict[str, Any]] = []
        model_pattern = re.compile(
            r"class\s+(\w+)\((?:.*?Base.*?|.*?Model.*?|.*?db\.Model.*?)\):",
            re.DOTALL,
        )
        column_pattern = re.compile(
            r"(\w+)(?::\s*Mapped\[.*?\])?\s*=\s*(?:mapped_column|Column|db\.Column)\("
            r"(.*?)\)",
            re.DOTALL,
        )

        for pyfile in root.rglob("*.py"):
            if any(part in _IGNORE_DIRS for part in pyfile.parts):
                continue
            if pyfile.stat().st_size > MAX_FILE_SIZE:
                continue
            try:
                content = pyfile.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            for model_match in model_pattern.finditer(content):
                model_name = model_match.group(1)
                # Extract the class body (up to the next class or EOF)
                start = model_match.end()
                next_class = re.search(r"\nclass\s+\w+", content[start:])
                end = start + next_class.start() if next_class else len(content)
                body = content[start:end]

                # Find tablename
                table_match = re.search(
                    r'__tablename__\s*=\s*["\'](\w+)["\']', body
                )
                table_name = table_match.group(1) if table_match else model_name.lower() + "s"

                # Find columns
                columns: list[dict[str, str]] = []
                for col_match in column_pattern.finditer(body):
                    col_name = col_match.group(1)
                    col_def = col_match.group(2).strip()
                    columns.append({"name": col_name, "definition": col_def[:100]})

                models_found.append({
                    "name": model_name,
                    "table": table_name,
                    "columns": columns,
                    "file": str(pyfile.relative_to(root)),
                })

        return models_found

    def _extract_env_vars(self, root: Path) -> list[dict[str, str]]:
        """Find environment variable references in the codebase."""
        env_vars: dict[str, dict[str, str]] = {}
        env_pattern = re.compile(
            r'os\.environ\.get\(\s*["\'](\w+)["\']'
            r'|os\.getenv\(\s*["\'](\w+)["\']'
            r'|(\w+):\s*str\s*=\s*["\"]',
        )

        # Check .env.example first
        env_example = root / ".env.example"
        if env_example.exists():
            try:
                for line in env_example.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key = line.split("=", 1)[0].strip()
                        env_vars[key] = {"name": key, "source": ".env.example"}
            except Exception:
                pass

        # Check Settings class / config files
        for pyfile in root.rglob("*.py"):
            if any(part in _IGNORE_DIRS for part in pyfile.parts):
                continue
            if pyfile.stat().st_size > MAX_FILE_SIZE:
                continue
            try:
                content = pyfile.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            for match in env_pattern.finditer(content):
                var_name = match.group(1) or match.group(2) or match.group(3)
                if var_name and var_name.isupper():
                    env_vars.setdefault(var_name, {
                        "name": var_name,
                        "source": str(pyfile.relative_to(root)),
                    })

        return list(env_vars.values())

    def _extract_dependencies(self, root: Path) -> dict[str, list[str]]:
        """Detect project dependencies from requirements/package files."""
        deps: dict[str, list[str]] = {}

        # Python
        for req_file in ["requirements.txt", "requirements-dev.txt"]:
            path = root / req_file
            if path.exists():
                try:
                    lines = [
                        l.split("#")[0].strip()
                        for l in path.read_text().splitlines()
                        if l.strip() and not l.strip().startswith("#")
                    ]
                    deps[req_file] = lines
                except Exception:
                    pass

        # pyproject.toml
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text()
                dep_match = re.search(
                    r"dependencies\s*=\s*\[(.*?)\]", content, re.DOTALL
                )
                if dep_match:
                    raw = dep_match.group(1)
                    packages = re.findall(r'"([^"]+)"', raw)
                    deps["pyproject.toml"] = packages
            except Exception:
                pass

        # Node.js
        package_json = root / "package.json"
        if package_json.exists():
            try:
                data = json.loads(package_json.read_text())
                for key in ["dependencies", "devDependencies"]:
                    if key in data:
                        deps[f"package.json ({key})"] = list(data[key].keys())
            except Exception:
                pass

        return deps

    # -----------------------------------------------------------------------
    # Section builders
    # -----------------------------------------------------------------------

    async def _generate_architecture_section(
        self,
        project_name: str,
        file_tree: str,
        code_summary: str,
        context: dict,
    ) -> WikiSection:
        """Use AI to generate an architecture overview."""
        provider, model = resolve_model("documentation")

        prompt = (
            f"Generate a concise architecture overview for the project '{project_name}'.\n\n"
            f"File tree:\n```\n{file_tree[:3000]}\n```\n\n"
            f"Code summary:\n```\n{code_summary[:3000]}\n```\n\n"
            "Include: high-level architecture, key components, data flow, "
            "and technology stack. Use markdown formatting."
        )

        content = await call_model(
            provider,
            model,
            [{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=2048,
        )

        return WikiSection(title="Architecture Overview", content=content)

    def _build_api_section(self, routes: list[dict[str, str]]) -> WikiSection:
        """Build an API Reference section from extracted routes."""
        lines: list[str] = []
        # Group by file
        by_file: dict[str, list[dict[str, str]]] = {}
        for r in routes:
            by_file.setdefault(r["file"], []).append(r)

        for file_path, file_routes in sorted(by_file.items()):
            lines.append(f"### `{file_path}`\n")
            lines.append("| Method | Path | Function | Description |")
            lines.append("|--------|------|----------|-------------|")
            for r in file_routes:
                desc = r["description"][:80] if r["description"] else ""
                response = f" → `{r['response_model']}`" if r["response_model"] else ""
                lines.append(
                    f"| `{r['method']}` | `{r['path']}` | "
                    f"`{r['function']}`{response} | {desc} |"
                )
            lines.append("")

        return WikiSection(
            title="API Reference",
            content="\n".join(lines),
        )

    def _build_schema_section(self, models: list[dict[str, Any]]) -> WikiSection:
        """Build a Database Schema section from extracted models."""
        lines: list[str] = []
        for m in models:
            lines.append(f"### `{m['name']}` (table: `{m['table']}`)")
            lines.append(f"*Defined in `{m['file']}`*\n")
            if m["columns"]:
                lines.append("| Column | Definition |")
                lines.append("|--------|-----------|")
                for col in m["columns"]:
                    lines.append(f"| `{col['name']}` | `{col['definition']}` |")
            lines.append("")

        return WikiSection(
            title="Database Schema",
            content="\n".join(lines),
        )

    def _build_setup_section(
        self,
        project_name: str,
        dependencies: dict[str, list[str]],
        env_vars: list[dict[str, str]],
    ) -> WikiSection:
        """Build a Setup Guide section."""
        lines: list[str] = [f"### Setting up {project_name}\n"]

        # Dependencies
        if dependencies:
            lines.append("#### Dependencies\n")
            for source, pkgs in dependencies.items():
                lines.append(f"**{source}:**")
                lines.append("```")
                for pkg in pkgs[:30]:
                    lines.append(pkg)
                if len(pkgs) > 30:
                    lines.append(f"... and {len(pkgs) - 30} more")
                lines.append("```\n")

        # Quick start
        lines.append("#### Quick Start\n")
        lines.append("```bash")
        if any("requirements" in k for k in dependencies):
            lines.append("# Create virtual environment")
            lines.append("python -m venv venv")
            lines.append("source venv/bin/activate")
            lines.append("pip install -r requirements.txt")
        elif any("package.json" in k for k in dependencies):
            lines.append("npm install")
        lines.append("```\n")

        if env_vars:
            lines.append("#### Environment Variables\n")
            lines.append("Copy `.env.example` to `.env` and fill in the values.\n")

        return WikiSection(title="Setup Guide", content="\n".join(lines))

    def _build_env_section(self, env_vars: list[dict[str, str]]) -> WikiSection:
        """Build an Environment Variables section."""
        lines: list[str] = [
            "| Variable | Source |",
            "|----------|--------|",
        ]
        for var in sorted(env_vars, key=lambda v: v["name"]):
            lines.append(f"| `{var['name']}` | `{var.get('source', '')}` |")

        return WikiSection(
            title="Environment Variables",
            content="\n".join(lines),
        )

    # -----------------------------------------------------------------------
    # Render
    # -----------------------------------------------------------------------

    def _render_markdown(
        self, project_name: str, sections: list[WikiSection]
    ) -> str:
        """Render all sections into a single markdown document."""
        parts: list[str] = [f"# {project_name} Wiki\n"]
        # Table of contents
        parts.append("## Table of Contents\n")
        for i, s in enumerate(sections, 1):
            anchor = s.title.lower().replace(" ", "-")
            parts.append(f"{i}. [{s.title}](#{anchor})")
        parts.append("")

        for section in sections:
            parts.append(f"## {section.title}\n")
            parts.append(section.content)
            parts.append("")
            for sub in section.subsections:
                parts.append(f"### {sub.title}\n")
                parts.append(sub.content)
                parts.append("")

        return "\n".join(parts)
