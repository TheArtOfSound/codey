from __future__ import annotations

import ast
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from codey.saas.intelligence.providers import MODELS, call_model, resolve_model
from codey.saas.intelligence.router import ExecutionMode, TaskConfig

logger = logging.getLogger(__name__)

MAX_AUTO_FIX_RETRIES = 3

# ---------------------------------------------------------------------------
# Assessment result
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    severity: str  # "error", "warning", "info"
    message: str
    line: int | None = None


@dataclass
class AssessmentResult:
    score: float  # 0.0–1.0
    issues: list[Issue] = field(default_factory=list)
    passed: bool = True


@dataclass
class ExecutionResult:
    content: str
    model_used: str
    provider_used: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    assessment: AssessmentResult | None = None


# ---------------------------------------------------------------------------
# Model Ensemble
# ---------------------------------------------------------------------------


class ModelEnsemble:
    """Execute tasks using one or more models and optionally auto-fix."""

    async def execute(
        self,
        config: TaskConfig,
        messages: list[dict[str, str]],
        context: dict | None = None,
    ) -> ExecutionResult:
        """Run the task according to *config.mode*.

        Returns the best :class:`ExecutionResult`.
        """
        context = context or {}

        if config.mode == ExecutionMode.SINGLE:
            return await self._execute_single(config, messages, context)
        elif config.mode == ExecutionMode.PARALLEL:
            return await self._execute_parallel(config, messages, context)
        elif config.mode == ExecutionMode.REASON_THEN_IMPLEMENT:
            return await self._execute_reason_then_implement(
                config, messages, context
            )
        else:
            return await self._execute_single(config, messages, context)

    # -----------------------------------------------------------------------
    # Execution strategies
    # -----------------------------------------------------------------------

    async def _execute_single(
        self,
        config: TaskConfig,
        messages: list[dict[str, str]],
        context: dict,
    ) -> ExecutionResult:
        """Call a single model and return the result."""
        provider, model = resolve_model(config.primary)
        result = await self._call_and_measure(
            provider, model, messages, config
        )

        # Assess the output if it looks like code
        if self._looks_like_code(result.content):
            result.assessment = await self.assess_output(result.content, context)
            if not result.assessment.passed:
                fixed = await self.auto_fix(
                    result.content, result.assessment.issues, context
                )
                if fixed != result.content:
                    result.content = fixed
                    result.assessment = await self.assess_output(fixed, context)

        return result

    async def _execute_parallel(
        self,
        config: TaskConfig,
        messages: list[dict[str, str]],
        context: dict,
    ) -> ExecutionResult:
        """Call primary and secondary models in parallel, pick the best."""
        primary_provider, primary_model = resolve_model(config.primary)
        tasks = [
            self._call_and_measure(primary_provider, primary_model, messages, config)
        ]

        if config.secondary:
            try:
                sec_provider, sec_model = resolve_model(config.secondary)
                tasks.append(
                    self._call_and_measure(
                        sec_provider, sec_model, messages, config
                    )
                )
            except RuntimeError:
                logger.warning(
                    "Secondary model '%s' unavailable, running single",
                    config.secondary,
                )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions
        valid_results: list[ExecutionResult] = [
            r for r in results if isinstance(r, ExecutionResult)
        ]
        if not valid_results:
            # All failed — re-raise the first exception
            for r in results:
                if isinstance(r, BaseException):
                    raise r
            raise RuntimeError("All parallel model calls failed")

        # Assess each result
        for r in valid_results:
            if self._looks_like_code(r.content):
                r.assessment = await self.assess_output(r.content, context)

        # Pick the best by assessment score (or first if no assessments)
        best = max(
            valid_results,
            key=lambda r: r.assessment.score if r.assessment else 0.5,
        )
        logger.info(
            "Parallel: picked %s/%s (score=%.2f) over %d candidates",
            best.provider_used,
            best.model_used,
            best.assessment.score if best.assessment else 0.5,
            len(valid_results),
        )
        return best

    async def _execute_reason_then_implement(
        self,
        config: TaskConfig,
        messages: list[dict[str, str]],
        context: dict,
    ) -> ExecutionResult:
        """First call a reasoning model, then pass its plan to an impl model."""
        # Step 1: Reasoning
        reasoning_provider, reasoning_model = resolve_model(config.primary)
        reasoning_messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    "Before writing any code, analyze this request step by step. "
                    "Consider edge cases, error handling, security implications, "
                    "and the best approach. Provide a detailed plan."
                ),
            },
        ]
        reasoning_result = await self._call_and_measure(
            reasoning_provider, reasoning_model, reasoning_messages, config
        )

        # Step 2: Implementation using the reasoning output
        impl_key = config.secondary or "code_generation"
        impl_provider, impl_model = resolve_model(impl_key)

        impl_messages = [
            *messages,
            {
                "role": "assistant",
                "content": f"Here is my analysis and plan:\n\n{reasoning_result.content}",
            },
            {
                "role": "user",
                "content": (
                    "Now implement the solution based on the analysis above. "
                    "Write clean, production-quality code."
                ),
            },
        ]
        impl_result = await self._call_and_measure(
            impl_provider, impl_model, impl_messages, config
        )

        # Combine token counts
        impl_result.tokens_in += reasoning_result.tokens_in
        impl_result.tokens_out += reasoning_result.tokens_out
        impl_result.latency_ms += reasoning_result.latency_ms

        # Assess
        if self._looks_like_code(impl_result.content):
            impl_result.assessment = await self.assess_output(
                impl_result.content, context
            )
            if not impl_result.assessment.passed:
                fixed = await self.auto_fix(
                    impl_result.content, impl_result.assessment.issues, context
                )
                if fixed != impl_result.content:
                    impl_result.content = fixed
                    impl_result.assessment = await self.assess_output(
                        fixed, context
                    )

        return impl_result

    # -----------------------------------------------------------------------
    # Assessment
    # -----------------------------------------------------------------------

    async def assess_output(
        self, code: str, context: dict | None = None
    ) -> AssessmentResult:
        """Run static checks on *code* and return an assessment.

        Checks:
        - Python syntax validity (via ``ast.parse``)
        - Unresolved imports pattern (bare ``import *``)
        - Common security anti-patterns
        - Naming consistency (snake_case for Python)
        """
        context = context or {}
        issues: list[Issue] = []
        language = context.get("language", self._detect_language(code))

        # --- Syntax check (Python) ---
        if language == "python":
            # Extract Python code blocks if wrapped in markdown
            python_blocks = self._extract_code_blocks(code, "python")
            blocks_to_check = python_blocks if python_blocks else [code]

            for block in blocks_to_check:
                try:
                    ast.parse(block)
                except SyntaxError as e:
                    issues.append(
                        Issue(
                            severity="error",
                            message=f"Python syntax error: {e.msg}",
                            line=e.lineno,
                        )
                    )

        # --- Wildcard import ---
        if re.search(r"^from\s+\S+\s+import\s+\*", code, re.MULTILINE):
            issues.append(
                Issue(
                    severity="warning",
                    message="Wildcard import detected — prefer explicit imports",
                )
            )

        # --- Security anti-patterns ---
        security_patterns = [
            (r"\beval\s*\(", "Use of eval() — potential code injection"),
            (r"\bexec\s*\(", "Use of exec() — potential code injection"),
            (r"\bos\.system\s*\(", "Use of os.system() — prefer subprocess"),
            (r"password\s*=\s*['\"][^'\"]+['\"]", "Hardcoded password detected"),
            (r"(?:api[_-]?key|secret)\s*=\s*['\"][^'\"]+['\"]", "Hardcoded secret detected"),
            (r"__import__\s*\(", "Dynamic import — review carefully"),
            (r"pickle\.loads?\s*\(", "Pickle deserialization — potential RCE"),
        ]
        for pattern, message in security_patterns:
            matches = list(re.finditer(pattern, code, re.IGNORECASE))
            for m in matches:
                line_no = code[: m.start()].count("\n") + 1
                issues.append(
                    Issue(severity="warning", message=message, line=line_no)
                )

        # --- Naming consistency (Python) ---
        if language == "python":
            # Check for camelCase function definitions (should be snake_case)
            camel_funcs = re.findall(
                r"def\s+([a-z]+[A-Z][a-zA-Z]*)\s*\(", code
            )
            for name in camel_funcs:
                issues.append(
                    Issue(
                        severity="info",
                        message=f"Function '{name}' uses camelCase — Python convention is snake_case",
                    )
                )

        # Compute score
        error_count = sum(1 for i in issues if i.severity == "error")
        warning_count = sum(1 for i in issues if i.severity == "warning")
        info_count = sum(1 for i in issues if i.severity == "info")

        score = 1.0 - (error_count * 0.3) - (warning_count * 0.1) - (info_count * 0.02)
        score = max(0.0, min(1.0, score))
        passed = error_count == 0

        return AssessmentResult(score=score, issues=issues, passed=passed)

    # -----------------------------------------------------------------------
    # Auto-fix
    # -----------------------------------------------------------------------

    async def auto_fix(
        self, code: str, issues: list[Issue], context: dict | None = None
    ) -> str:
        """Attempt to fix *issues* in *code* using a model, up to retries."""
        context = context or {}

        # Only attempt to fix errors, not warnings/info
        errors = [i for i in issues if i.severity == "error"]
        if not errors:
            return code

        issue_descriptions = "\n".join(
            f"- Line {i.line or '?'}: {i.message}" for i in errors
        )

        for attempt in range(1, MAX_AUTO_FIX_RETRIES + 1):
            logger.info("Auto-fix attempt %d/%d", attempt, MAX_AUTO_FIX_RETRIES)
            try:
                provider, model = resolve_model("debugging")
                fix_messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are a code repair assistant. Fix the issues "
                            "in the code below while preserving its functionality. "
                            "Return ONLY the corrected code, no explanations."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Fix these issues:\n{issue_descriptions}\n\n"
                            f"Code:\n```\n{code}\n```"
                        ),
                    },
                ]
                fixed = await call_model(
                    provider,
                    model,
                    fix_messages,
                    temperature=0.1,
                    max_tokens=min(len(code) * 2, 16_384),
                )

                # Extract code from markdown fences if present
                blocks = self._extract_code_blocks(fixed)
                if blocks:
                    fixed = blocks[0]

                # Verify the fix resolved issues
                assessment = await self.assess_output(fixed, context)
                if assessment.passed:
                    logger.info("Auto-fix succeeded on attempt %d", attempt)
                    return fixed

                # Update issues for next attempt
                errors = [i for i in assessment.issues if i.severity == "error"]
                issue_descriptions = "\n".join(
                    f"- Line {i.line or '?'}: {i.message}" for i in errors
                )
                code = fixed

            except Exception:
                logger.exception("Auto-fix attempt %d failed", attempt)

        logger.warning("Auto-fix exhausted %d retries", MAX_AUTO_FIX_RETRIES)
        return code

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    async def _call_and_measure(
        self,
        provider: str,
        model: str,
        messages: list[dict[str, str]],
        config: TaskConfig,
    ) -> ExecutionResult:
        """Call a model and return an :class:`ExecutionResult` with timing."""
        t0 = time.monotonic()
        content = await call_model(
            provider,
            model,
            messages,
            temperature=config.temperature,
            max_tokens=config.estimated_tokens,
        )
        latency_ms = (time.monotonic() - t0) * 1000

        # Rough token estimates (actual counts require response metadata)
        tokens_in = sum(len(m.get("content", "").split()) * 1.3 for m in messages)
        tokens_out = len(content.split()) * 1.3

        return ExecutionResult(
            content=content,
            model_used=model,
            provider_used=provider,
            tokens_in=int(tokens_in),
            tokens_out=int(tokens_out),
            latency_ms=round(latency_ms, 1),
        )

    @staticmethod
    def _looks_like_code(text: str) -> bool:
        """Heuristic: does *text* contain code-like content?"""
        code_indicators = [
            r"```",              # markdown code block
            r"def\s+\w+\s*\(",  # python function
            r"class\s+\w+",     # class definition
            r"function\s+\w+",  # JS function
            r"import\s+",       # import statement
            r"const\s+\w+",     # JS const
            r"#include",        # C/C++ include
        ]
        return any(re.search(p, text) for p in code_indicators)

    @staticmethod
    def _extract_code_blocks(text: str, language: str | None = None) -> list[str]:
        """Extract fenced code blocks from markdown text."""
        if language:
            pattern = rf"```{re.escape(language)}\s*\n(.*?)```"
        else:
            pattern = r"```(?:\w*)\s*\n(.*?)```"
        return re.findall(pattern, text, re.DOTALL)

    @staticmethod
    def _detect_language(code: str) -> str:
        """Best-effort language detection from code content."""
        if re.search(r"\bdef\s+\w+\s*\(|import\s+\w+|from\s+\w+\s+import", code):
            return "python"
        if re.search(r"\bfunction\s+\w+|const\s+\w+|let\s+\w+|=>\s*{", code):
            return "javascript"
        if re.search(r"\bfn\s+\w+|let\s+mut\s+|impl\s+\w+|use\s+\w+::", code):
            return "rust"
        if re.search(r"\bfunc\s+\w+|package\s+\w+|go\s+func", code):
            return "go"
        return "unknown"
