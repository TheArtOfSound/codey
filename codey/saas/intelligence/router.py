from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from codey.saas.intelligence.providers import MODELS, get_available_providers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class TaskType(str, Enum):
    CODE_GENERATION = "code_generation"
    DEBUGGING = "debugging"
    ARCHITECTURE = "architecture"
    SECURITY = "security_audit"
    DOCUMENTATION = "documentation"
    TEST_GENERATION = "test_generation"
    CODE_REVIEW = "code_review"
    REFACTOR = "code_generation"
    FAST_CODE = "fast_code"
    LONG_CONTEXT = "long_context"
    DEFAULT = "default"


class ExecutionMode(str, Enum):
    SINGLE = "single"
    PARALLEL = "parallel"
    REASON_THEN_IMPLEMENT = "reason_then_implement"


@dataclass
class TaskConfig:
    """Routing decision produced by :class:`TaskRouter`."""

    primary: str  # model key from MODELS
    secondary: str | None = None
    mode: ExecutionMode = ExecutionMode.SINGLE
    estimated_tokens: int = 4096
    temperature: float = 0.3
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Keyword / heuristic tables
# ---------------------------------------------------------------------------

_TASK_KEYWORDS: dict[str, list[str]] = {
    "debugging": [
        "fix", "bug", "error", "crash", "broken", "issue", "traceback",
        "exception", "failing", "wrong", "doesn't work", "not working",
        "debug", "stacktrace", "segfault", "TypeError", "ValueError",
        "KeyError", "AttributeError", "ImportError", "undefined",
    ],
    "security_audit": [
        "security", "vulnerability", "CVE", "injection", "XSS", "CSRF",
        "auth", "authentication", "authorization", "encrypt", "hash",
        "password", "token", "OAuth", "sanitize", "escape", "audit",
        "penetration", "pentest", "OWASP",
    ],
    "architecture": [
        "architect", "design", "system", "structure", "pattern", "refactor",
        "microservice", "monolith", "scalab", "database design", "schema",
        "API design", "infrastructure", "deploy", "cloud", "high-level",
        "trade-off", "tradeoff",
    ],
    "documentation": [
        "document", "docstring", "README", "wiki", "explain", "comment",
        "annotate", "description", "guide", "tutorial", "how to",
        "changelog", "JSDoc", "pydoc", "API docs",
    ],
    "test_generation": [
        "test", "spec", "unittest", "pytest", "jest", "mocha", "coverage",
        "TDD", "BDD", "assertion", "mock", "stub", "fixture", "e2e",
        "integration test", "unit test",
    ],
    "code_review": [
        "review", "PR", "pull request", "code quality", "lint", "style",
        "best practice", "smell", "clean code", "feedback",
    ],
    "code_generation": [
        "build", "create", "implement", "write", "generate", "code",
        "function", "class", "module", "feature", "endpoint", "API",
        "scaffold", "boilerplate", "CRUD",
    ],
}

_QUALITY_KEYWORDS: list[str] = [
    "thorough", "careful", "best", "production", "robust", "enterprise",
    "complex", "critical", "important", "detailed",
]

_SPEED_KEYWORDS: list[str] = [
    "quick", "fast", "simple", "snippet", "brief", "short", "small",
    "one-liner", "minimal", "prototype", "sketch",
]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class TaskRouter:
    """Classifies a user request and selects models + execution mode."""

    def classify(self, request: str, context: dict | None = None) -> TaskConfig:
        """Analyze *request* and return a :class:`TaskConfig`.

        *context* may include:
        - ``codebase_files``: int — number of files in the project
        - ``codebase_tokens``: int — estimated token count of the project
        - ``language``: str — primary language
        - ``mode``: ``"fast"`` or ``"quality"``
        """
        context = context or {}
        request_lower = request.lower()

        task_type = self._classify_task_type(request_lower)
        estimated_tokens = self._estimate_output_tokens(request_lower, context)
        execution_mode = self._select_mode(task_type, request_lower, context)
        temperature = self._select_temperature(task_type)
        primary, secondary = self._select_models(
            task_type, execution_mode, context
        )

        # If the codebase is very large, prefer long-context model
        codebase_tokens = context.get("codebase_tokens", 0)
        if codebase_tokens > 100_000 and task_type not in (
            TaskType.FAST_CODE,
            TaskType.DOCUMENTATION,
        ):
            primary = "long_context"

        config = TaskConfig(
            primary=primary,
            secondary=secondary,
            mode=execution_mode,
            estimated_tokens=estimated_tokens,
            temperature=temperature,
            metadata={
                "task_type": task_type.value,
                "codebase_tokens": codebase_tokens,
            },
        )
        logger.info(
            "Routed task → %s (mode=%s, primary=%s, secondary=%s, est_tokens=%d)",
            task_type.value,
            execution_mode.value,
            primary,
            secondary,
            estimated_tokens,
        )
        return config

    # -----------------------------------------------------------------------
    # Internal classification helpers
    # -----------------------------------------------------------------------

    def _classify_task_type(self, text: str) -> TaskType:
        """Score each task type by keyword hits and return the best match."""
        scores: dict[str, int] = {}
        for task_key, keywords in _TASK_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw.lower() in text)
            if score > 0:
                scores[task_key] = score

        if not scores:
            return TaskType.DEFAULT

        best = max(scores, key=lambda k: scores[k])
        return TaskType(best)

    def _estimate_output_tokens(self, text: str, context: dict) -> int:
        """Rough estimate of how many tokens the response will need."""
        # Base estimate from prompt length
        prompt_words = len(text.split())
        base = max(1024, prompt_words * 4)

        # Scale up for code generation tasks
        if any(kw in text for kw in ["full", "complete", "entire", "whole"]):
            base = max(base, 8192)

        # Scale up for multi-file requests
        file_mentions = len(re.findall(r"\b\w+\.\w{1,4}\b", text))
        if file_mentions > 3:
            base = max(base, 4096 * file_mentions)

        # Cap at model limits
        return min(base, 32_768)

    def _select_mode(
        self, task_type: TaskType, text: str, context: dict
    ) -> ExecutionMode:
        """Decide between single, parallel, or reason-then-implement."""
        user_mode = context.get("mode", "")

        # Fast mode always single
        if user_mode == "fast":
            return ExecutionMode.SINGLE

        # Architecture and security benefit from reasoning first
        if task_type in (TaskType.ARCHITECTURE, TaskType.SECURITY):
            return ExecutionMode.REASON_THEN_IMPLEMENT

        # Quality mode on code generation uses parallel for comparison
        if user_mode == "quality" and task_type == TaskType.CODE_GENERATION:
            return ExecutionMode.PARALLEL

        # Complex requests (long prompt + quality keywords)
        if len(text.split()) > 100 and any(kw in text for kw in _QUALITY_KEYWORDS):
            return ExecutionMode.REASON_THEN_IMPLEMENT

        return ExecutionMode.SINGLE

    def _select_temperature(self, task_type: TaskType) -> float:
        """Pick a temperature appropriate for the task."""
        temps: dict[TaskType, float] = {
            TaskType.CODE_GENERATION: 0.2,
            TaskType.DEBUGGING: 0.1,
            TaskType.ARCHITECTURE: 0.4,
            TaskType.SECURITY: 0.1,
            TaskType.DOCUMENTATION: 0.5,
            TaskType.TEST_GENERATION: 0.2,
            TaskType.CODE_REVIEW: 0.3,
            TaskType.FAST_CODE: 0.2,
            TaskType.LONG_CONTEXT: 0.3,
            TaskType.DEFAULT: 0.3,
        }
        return temps.get(task_type, 0.3)

    def _select_models(
        self,
        task_type: TaskType,
        mode: ExecutionMode,
        context: dict,
    ) -> tuple[str, str | None]:
        """Choose primary (and optionally secondary) model keys."""
        available = get_available_providers()

        # Map task type to model key
        primary_key = task_type.value
        if primary_key not in MODELS:
            primary_key = "default"

        # Verify the primary model's provider is available
        primary_provider = MODELS[primary_key]["provider"]
        if primary_provider not in available:
            primary_key = "default"
            default_provider = MODELS["default"]["provider"]
            if default_provider not in available:
                # Fall back to whatever is available
                for key, spec in MODELS.items():
                    if spec["provider"] in available:
                        primary_key = key
                        break

        # Pick secondary for parallel / reason-then-implement modes
        secondary_key: str | None = None
        if mode == ExecutionMode.PARALLEL:
            # Use a second model for comparison
            candidates = ["code_generation", "fast_code", "debugging", "default"]
            for c in candidates:
                if c != primary_key and MODELS[c]["provider"] in available:
                    secondary_key = c
                    break

        elif mode == ExecutionMode.REASON_THEN_IMPLEMENT:
            # Reasoning model first, then implementation model
            reasoning_candidates = ["architecture", "security_audit", "debugging"]
            for c in reasoning_candidates:
                if c != primary_key and MODELS[c]["provider"] in available:
                    secondary_key = primary_key
                    primary_key = c  # reasoning model becomes primary
                    break

        # Speed override
        user_mode = context.get("mode", "")
        if user_mode == "fast" and "groq" in available:
            primary_key = "fast_code"
            secondary_key = None

        return primary_key, secondary_key
