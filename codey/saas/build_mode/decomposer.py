"""Task Decomposition Engine — breaks project plans into dependency-ordered task lists."""

from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# File type classification priorities (lower = earlier phase)
_TYPE_PRIORITY: dict[str, int] = {
    "config": 0,
    "model": 1,
    "schema": 2,
    "service": 3,
    "middleware": 4,
    "router": 5,
    "component": 6,
    "page": 7,
    "hook": 6,
    "util": 2,
    "test": 8,
    "docker": 9,
    "migration": 0,
    "static": 0,
    "style": 6,
}

# Patterns for classifying files by path
_FILE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("config", re.compile(r"(\.env|config|settings|\.toml|\.yaml|\.yml|\.json|Dockerfile|docker-compose|Makefile|pyproject|package\.json|tsconfig|vite\.config|next\.config|tailwind\.config|postcss\.config)", re.IGNORECASE)),
    ("docker", re.compile(r"(Dockerfile|docker-compose|\.dockerignore)", re.IGNORECASE)),
    ("migration", re.compile(r"(migration|alembic|versions/)", re.IGNORECASE)),
    ("model", re.compile(r"(models?/|model\.|schema\.|entities?/|entity\.)", re.IGNORECASE)),
    ("schema", re.compile(r"(schemas?/|schema\.|types?\.|interfaces?/)", re.IGNORECASE)),
    ("middleware", re.compile(r"(middleware|interceptor|guard)", re.IGNORECASE)),
    ("service", re.compile(r"(services?/|service\.|utils?/|util\.|helpers?/|helper\.|lib/)", re.IGNORECASE)),
    ("router", re.compile(r"(routes?/|router|controllers?/|controller\.|api/|endpoints?/|views?/)", re.IGNORECASE)),
    ("hook", re.compile(r"(hooks?/|use[A-Z])", re.IGNORECASE)),
    ("component", re.compile(r"(components?/|widgets?/|ui/)", re.IGNORECASE)),
    ("page", re.compile(r"(pages?/|views?/|screens?/|app/.*page\.|app/.*layout\.)", re.IGNORECASE)),
    ("style", re.compile(r"\.(css|scss|sass|less|styled)$", re.IGNORECASE)),
    ("static", re.compile(r"(public/|static/|assets/)", re.IGNORECASE)),
    ("test", re.compile(r"(tests?/|test_|_test\.|\.test\.|\.spec\.|__tests__/|specs?/)", re.IGNORECASE)),
]


@dataclass
class TaskNode:
    """A single file-generation task in the build pipeline."""

    file_path: str
    phase: int
    dependencies: list[str] = field(default_factory=list)
    file_type: str = "unknown"
    estimated_lines: int = 100
    status: str = "pending"


class TaskDecomposer:
    """Decomposes a project plan into a dependency-ordered task list.

    The decomposition algorithm:
    1. Extract all files from the plan's file_tree
    2. Classify each file by type
    3. Infer dependencies based on file types and structural rules
    4. Assign phases based on dependency layers
    5. Topologically sort using Kahn's algorithm
    6. Return the ordered task list
    """

    def decompose(self, project_plan: dict) -> list[TaskNode]:
        """Take a project plan and produce a dependency-ordered task list.

        Parameters
        ----------
        project_plan : dict
            Must contain at minimum a ``file_tree`` key mapping file paths
            to their type hints (or empty strings). May also contain a
            ``phases`` key with pre-defined phase groupings.

        Returns
        -------
        list[TaskNode]
            Topologically sorted task list ready for sequential generation.
        """
        file_tree = project_plan.get("file_tree", {})
        phases_spec = project_plan.get("phases", [])

        if not file_tree:
            logger.warning("Empty file_tree in project plan — nothing to decompose")
            return []

        # Step 1: Create task nodes for every file
        tasks: list[TaskNode] = []
        task_map: dict[str, TaskNode] = {}

        for file_path, file_hint in file_tree.items():
            file_type = self._classify_file(file_path, file_hint)
            estimated_lines = self._estimate_lines(file_path, file_type)
            task = TaskNode(
                file_path=file_path,
                phase=0,
                file_type=file_type,
                estimated_lines=estimated_lines,
            )
            tasks.append(task)
            task_map[file_path] = task

        # Step 2: Infer dependencies between tasks
        self._infer_dependencies(tasks, task_map)

        # Step 3: Assign phases — use plan phases if available, otherwise compute
        if phases_spec:
            self._assign_phases_from_spec(tasks, task_map, phases_spec)
        else:
            self._assign_phases_from_deps(tasks, task_map)

        # Step 4: Topological sort
        sorted_tasks = self._topological_sort(tasks, task_map)

        logger.info(
            "Decomposed %d files into %d phases",
            len(sorted_tasks),
            max((t.phase for t in sorted_tasks), default=0) + 1,
        )

        return sorted_tasks

    def _classify_file(self, file_path: str, file_hint: str = "") -> str:
        """Classify a file by its path and optional type hint.

        Checks hint first (if the plan already specifies), then falls back
        to pattern matching on the file path.
        """
        # If the plan already told us the type, use it
        if file_hint and file_hint.lower() in _TYPE_PRIORITY:
            return file_hint.lower()

        # Pattern-match against path
        for file_type, pattern in _FILE_PATTERNS:
            if pattern.search(file_path):
                return file_type

        # Fallback heuristics by extension
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        if ext in ("py", "rb", "go", "rs", "java"):
            # Generic backend file — treat as service
            return "service"
        if ext in ("tsx", "jsx", "vue", "svelte"):
            return "component"
        if ext in ("ts", "js", "mjs"):
            return "service"
        if ext in ("css", "scss", "sass", "less"):
            return "style"
        if ext in ("html", "ejs", "hbs"):
            return "page"
        if ext in ("sql",):
            return "migration"
        if ext in ("md", "txt", "rst"):
            return "static"

        return "service"  # safe default

    def _estimate_lines(self, file_path: str, file_type: str) -> int:
        """Rough line count estimate based on file type."""
        estimates: dict[str, int] = {
            "config": 30,
            "migration": 40,
            "static": 20,
            "model": 80,
            "schema": 60,
            "service": 150,
            "middleware": 60,
            "router": 120,
            "component": 100,
            "page": 130,
            "hook": 50,
            "util": 80,
            "test": 120,
            "docker": 25,
            "style": 50,
        }
        base = estimates.get(file_type, 100)

        # Bump estimate for files with "index" or "main" in the name
        lower = file_path.lower()
        if "index" in lower or "main" in lower or "app" in lower:
            base = int(base * 1.3)

        return base

    def _infer_dependencies(
        self, tasks: list[TaskNode], task_map: dict[str, TaskNode]
    ) -> None:
        """Set dependencies based on file types and structural rules.

        Dependency rules:
        - Routers depend on services and models in the same domain
        - Services depend on models/schemas
        - Frontend components depend on hooks and types/schemas
        - Pages depend on components
        - Tests depend on the module they test
        - Config/migration/docker have no deps (they go first)
        """
        # Build indexes by type
        by_type: dict[str, list[TaskNode]] = defaultdict(list)
        for task in tasks:
            by_type[task.file_type].append(task)

        all_model_paths = {t.file_path for t in by_type.get("model", [])}
        all_schema_paths = {t.file_path for t in by_type.get("schema", [])}
        all_service_paths = {t.file_path for t in by_type.get("service", [])}
        all_component_paths = {t.file_path for t in by_type.get("component", [])}
        all_hook_paths = {t.file_path for t in by_type.get("hook", [])}
        all_config_paths = {t.file_path for t in by_type.get("config", [])}

        for task in tasks:
            deps: list[str] = []

            if task.file_type == "router":
                # Routers depend on domain-matching services and all models
                domain = self._extract_domain(task.file_path)
                for sp in all_service_paths:
                    if domain and domain in sp.lower():
                        deps.append(sp)
                # If no domain match, depend on all services
                if not deps:
                    deps.extend(all_service_paths)
                deps.extend(all_model_paths)
                deps.extend(all_schema_paths)

            elif task.file_type == "service":
                # Services depend on models and schemas
                deps.extend(all_model_paths)
                deps.extend(all_schema_paths)

            elif task.file_type == "middleware":
                # Middleware may depend on models (for user lookup, etc.)
                deps.extend(all_model_paths)

            elif task.file_type == "component":
                # Components depend on hooks and schemas/types
                deps.extend(all_hook_paths)
                deps.extend(all_schema_paths)

            elif task.file_type == "page":
                # Pages depend on components, hooks, and schemas
                deps.extend(all_component_paths)
                deps.extend(all_hook_paths)
                deps.extend(all_schema_paths)

            elif task.file_type == "hook":
                # Hooks depend on schemas/types and services (API layer)
                deps.extend(all_schema_paths)

            elif task.file_type == "test":
                # Tests depend on the file they're testing
                tested_path = self._find_tested_file(task.file_path, task_map)
                if tested_path:
                    deps.append(tested_path)

            elif task.file_type == "schema":
                # Schemas may depend on models
                deps.extend(all_model_paths)

            elif task.file_type == "model":
                # Models depend on config (database setup)
                deps.extend(all_config_paths)

            # Remove self-references and non-existent paths
            task.dependencies = [
                d for d in deps
                if d != task.file_path and d in task_map
            ]

    def _extract_domain(self, file_path: str) -> str:
        """Extract a domain name from a file path for dependency matching.

        e.g., ``routes/users.py`` -> ``user``
              ``api/auth_routes.py`` -> ``auth``
        """
        # Get filename without extension
        parts = file_path.replace("\\", "/").split("/")
        filename = parts[-1] if parts else file_path
        name = filename.rsplit(".", 1)[0] if "." in filename else filename

        # Strip common suffixes
        for suffix in ("_routes", "_router", "_controller", "_service", "_model",
                        "Routes", "Router", "Controller", "Service", "Model"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break

        # Singularize very basic cases
        if name.endswith("s") and not name.endswith("ss"):
            name = name[:-1]

        return name.lower()

    def _find_tested_file(
        self, test_path: str, task_map: dict[str, TaskNode]
    ) -> str | None:
        """Given a test file path, find the source file it tests."""
        # Common patterns: test_foo.py -> foo.py, foo_test.py -> foo.py,
        # foo.test.ts -> foo.ts, __tests__/foo.test.tsx -> ../foo.tsx
        parts = test_path.replace("\\", "/").split("/")
        filename = parts[-1]
        name_no_ext = filename.rsplit(".", 1)[0] if "." in filename else filename

        # Strip test prefixes/suffixes
        candidates: list[str] = []
        if name_no_ext.startswith("test_"):
            candidates.append(name_no_ext[5:])
        if name_no_ext.endswith("_test"):
            candidates.append(name_no_ext[:-5])
        if ".test" in filename:
            candidates.append(filename.replace(".test", ""))
        if ".spec" in filename:
            candidates.append(filename.replace(".spec", ""))

        for candidate_name in candidates:
            for path in task_map:
                p_filename = path.replace("\\", "/").split("/")[-1]
                if p_filename == candidate_name or p_filename.startswith(candidate_name + "."):
                    return path

        return None

    def _assign_phases_from_spec(
        self,
        tasks: list[TaskNode],
        task_map: dict[str, TaskNode],
        phases_spec: list[dict],
    ) -> None:
        """Assign phases based on the plan's explicit phase definitions."""
        # Build path -> phase mapping from spec
        path_to_phase: dict[str, int] = {}
        for i, phase_def in enumerate(phases_spec):
            for fp in phase_def.get("files", []):
                path_to_phase[fp] = i

        for task in tasks:
            if task.file_path in path_to_phase:
                task.phase = path_to_phase[task.file_path]
            else:
                # Fall back to type-based priority
                task.phase = _TYPE_PRIORITY.get(task.file_type, 5)

    def _assign_phases_from_deps(
        self,
        tasks: list[TaskNode],
        task_map: dict[str, TaskNode],
    ) -> None:
        """Assign phases based on dependency depth (longest path from root).

        Files with no dependencies get phase 0.
        Each file's phase = max(dep.phase for dep in dependencies) + 1.
        """
        # Compute depth via iterative relaxation
        depths: dict[str, int] = {t.file_path: 0 for t in tasks}
        changed = True
        iterations = 0
        max_iterations = len(tasks) + 1  # prevent infinite loops on cycles

        while changed and iterations < max_iterations:
            changed = False
            iterations += 1
            for task in tasks:
                for dep_path in task.dependencies:
                    new_depth = depths.get(dep_path, 0) + 1
                    if new_depth > depths[task.file_path]:
                        depths[task.file_path] = new_depth
                        changed = True

        for task in tasks:
            task.phase = depths[task.file_path]

    def _topological_sort(
        self,
        tasks: list[TaskNode],
        task_map: dict[str, TaskNode],
    ) -> list[TaskNode]:
        """Topologically sort tasks using Kahn's algorithm.

        Within the same topological layer, tasks are ordered by:
        1. Phase number (ascending)
        2. Type priority (ascending)
        3. File path (alphabetical, for determinism)

        Returns
        -------
        list[TaskNode]
            Sorted task list. If a cycle is detected, remaining tasks are
            appended in priority order with a warning.
        """
        # Build adjacency and in-degree maps
        in_degree: dict[str, int] = {t.file_path: 0 for t in tasks}
        adjacency: dict[str, list[str]] = defaultdict(list)

        for task in tasks:
            for dep_path in task.dependencies:
                if dep_path in in_degree:
                    adjacency[dep_path].append(task.file_path)
                    in_degree[task.file_path] += 1

        # Seed the queue with zero-in-degree nodes
        queue: list[TaskNode] = []
        for task in tasks:
            if in_degree[task.file_path] == 0:
                queue.append(task)

        # Sort the initial queue by phase -> type priority -> path
        queue.sort(
            key=lambda t: (
                t.phase,
                _TYPE_PRIORITY.get(t.file_type, 99),
                t.file_path,
            )
        )

        result: list[TaskNode] = []
        q = deque(queue)

        while q:
            task = q.popleft()
            result.append(task)

            # Collect newly freed tasks
            newly_free: list[TaskNode] = []
            for neighbor_path in adjacency.get(task.file_path, []):
                in_degree[neighbor_path] -= 1
                if in_degree[neighbor_path] == 0:
                    newly_free.append(task_map[neighbor_path])

            # Sort newly freed by phase -> priority -> path and add to queue
            newly_free.sort(
                key=lambda t: (
                    t.phase,
                    _TYPE_PRIORITY.get(t.file_type, 99),
                    t.file_path,
                )
            )
            q.extend(newly_free)

        # Cycle detection: if not all tasks were emitted, we have a cycle
        if len(result) < len(tasks):
            remaining = [t for t in tasks if t not in result]
            logger.warning(
                "Dependency cycle detected among %d files — appending in priority order",
                len(remaining),
            )
            remaining.sort(
                key=lambda t: (
                    t.phase,
                    _TYPE_PRIORITY.get(t.file_type, 99),
                    t.file_path,
                )
            )
            result.extend(remaining)

        return result

    def validate_order(self, tasks: list[TaskNode]) -> bool:
        """Verify no task comes before its dependencies in the list.

        Returns True if the ordering is valid.
        """
        position: dict[str, int] = {}
        for i, task in enumerate(tasks):
            position[task.file_path] = i

        for task in tasks:
            my_pos = position[task.file_path]
            for dep_path in task.dependencies:
                dep_pos = position.get(dep_path)
                if dep_pos is not None and dep_pos >= my_pos:
                    logger.error(
                        "Invalid order: %s (pos %d) depends on %s (pos %d)",
                        task.file_path,
                        my_pos,
                        dep_path,
                        dep_pos,
                    )
                    return False
        return True
