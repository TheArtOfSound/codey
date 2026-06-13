"""Validation System — syntax checking, import resolution, and phase validation."""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_PYTHON_SOURCE_ROOTS = ("src", "backend")
_VALIDATION_SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    ".next",
    ".nuxt",
    ".turbo",
    ".pytest_cache",
    "dist",
    "build",
}
_VALIDATION_SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".mjs",
    ".ts",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
}
_VALIDATION_DRAIN_TIMEOUT_SECONDS = 5.0
_MAX_VALIDATION_FILE_CHARS = 1_000_000
_URL_CREDENTIAL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|token|secret|password)=)[^&\s]+"
)
_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|token|secret|password|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


def _redact_validation_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _NAMED_SECRET_RE.sub(r"\1***", text)
    return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)


def _read_validation_file(path: Path) -> str | None:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.read(_MAX_VALIDATION_FILE_CHARS + 1)
    if len(content) > _MAX_VALIDATION_FILE_CHARS:
        return None
    return content


class FileValidator:
    """Validates generated source files for syntax, imports, and correctness.

    Supports Python and JavaScript/TypeScript files.
    """

    def validate_syntax(self, content: str, file_path: str) -> tuple[bool, str | None]:
        """Parse a file to check for syntax errors.

        Returns (passed, error_message). If passed is True, error_message is None.
        """
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""

        if ext == "py":
            return self._validate_python_syntax(content, file_path)
        elif ext in ("js", "jsx", "ts", "tsx", "mjs"):
            return self._validate_js_syntax(content, file_path)
        elif ext in ("json",):
            return self._validate_json_syntax(content, file_path)
        elif ext in ("yaml", "yml"):
            return self._validate_yaml_syntax(content, file_path)
        else:
            # For file types we can't validate, assume valid
            return True, None

    def validate_imports(
        self,
        content: str,
        file_path: str,
        existing_files: set[str],
    ) -> list[str]:
        """Check that local imports resolve to existing files.

        Returns a list of unresolved import errors (empty = all good).
        """
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        errors: list[str] = []

        if ext == "py":
            errors = self._validate_python_imports(content, file_path, existing_files)
        elif ext in ("js", "jsx", "ts", "tsx", "mjs"):
            errors = self._validate_js_imports(content, file_path, existing_files)

        return errors

    async def validate_phase(self, project_dir: str, phase: int) -> dict:
        """Run comprehensive validation on all files in a phase.

        Checks:
        1. Syntax validation on every file
        2. Cross-file import resolution
        3. Run pytest if Python test files exist
        4. Run eslint/tsc if JS/TS files exist

        Returns dict with test results and error lists.
        """
        project_path = Path(project_dir)
        if not project_path.is_dir():
            return {
                "tests_passed": 0,
                "tests_failed": 0,
                "import_errors": [],
                "lint_errors": [],
                "syntax_errors": [],
            }

        # Collect all files in the project
        project_root = project_path.resolve()
        all_files: dict[str, str] = {}  # path -> content
        existing_file_set: set[str] = set()
        skipped_file_warnings: list[str] = []
        for dirpath, dirnames, filenames in os.walk(project_root):
            dirnames[:] = sorted(
                dirname
                for dirname in dirnames
                if dirname not in _VALIDATION_SKIP_DIRS
            )
            for filename in sorted(filenames):
                fp = Path(dirpath) / filename
                if fp.suffix not in _VALIDATION_SOURCE_EXTENSIONS:
                    continue
                try:
                    resolved = fp.resolve()
                    if project_root not in {resolved, *resolved.parents}:
                        continue
                    rel_path = str(fp.relative_to(project_root))
                    existing_file_set.add(rel_path)
                    content = _read_validation_file(fp)
                    if content is None:
                        skipped_file_warnings.append(
                            f"{rel_path}: skipped validation because file exceeds "
                            f"{_MAX_VALIDATION_FILE_CHARS} characters"
                        )
                        continue
                    all_files[rel_path] = content
                except OSError:
                    continue

        syntax_errors: list[str] = []
        import_errors: list[str] = []
        lint_errors: list[str] = skipped_file_warnings

        # Syntax check all files
        for rel_path, content in all_files.items():
            passed, error = self.validate_syntax(content, rel_path)
            if not passed and error:
                syntax_errors.append(f"{rel_path}: {error}")

            # Import check
            imp_errors = self.validate_imports(content, rel_path, existing_file_set)
            import_errors.extend(imp_errors)

        # Try running pytest if Python test files exist
        tests_passed = 0
        tests_failed = 0

        py_test_files = [
            fp for fp in all_files
            if fp.endswith(".py") and ("test_" in fp or "_test.py" in fp)
        ]

        if py_test_files:
            test_result = await self._run_pytest(project_dir)
            tests_passed = test_result.get("passed", 0)
            tests_failed = test_result.get("failed", 0)
            lint_errors.extend(test_result.get("errors", []))

        # Try running tsc if TypeScript files exist
        ts_files = [fp for fp in all_files if fp.endswith((".ts", ".tsx"))]
        if ts_files:
            tsc_errors = await self._run_tsc(project_dir)
            lint_errors.extend(tsc_errors)

        return {
            "tests_passed": tests_passed,
            "tests_failed": tests_failed,
            "import_errors": import_errors,
            "lint_errors": lint_errors,
            "syntax_errors": syntax_errors,
        }

    # ------------------------------------------------------------------
    # Python validation
    # ------------------------------------------------------------------

    def _validate_python_syntax(
        self, content: str, file_path: str
    ) -> tuple[bool, str | None]:
        """Validate Python syntax using the ast module."""
        try:
            ast.parse(content, filename=file_path)
            return True, None
        except SyntaxError as e:
            return False, f"Line {e.lineno}: {e.msg}"

    def _validate_python_imports(
        self,
        content: str,
        file_path: str,
        existing_files: set[str],
    ) -> list[str]:
        """Check Python imports for local module resolution."""
        errors: list[str] = []

        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError:
            return errors  # Syntax errors handled separately

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # Only check local imports (no dots = likely stdlib/third-party)
                    module = alias.name
                    if self._is_local_python_module(module, existing_files):
                        if not self._resolve_python_module(module, file_path, existing_files):
                            errors.append(
                                f"{file_path}:{node.lineno}: Cannot resolve import '{module}'"
                            )

            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    module = node.module
                    if self._is_local_python_module(module, existing_files):
                        if not self._resolve_python_module(module, file_path, existing_files):
                            errors.append(
                                f"{file_path}:{node.lineno}: Cannot resolve 'from {module} import ...'"
                            )
                elif node.module and node.level > 0:
                    module = node.module
                    if not self._resolve_python_relative_module(
                        module, node.level, file_path, existing_files
                    ):
                        relative_module = "." * node.level + module
                        errors.append(
                            f"{file_path}:{node.lineno}: Cannot resolve relative import '{relative_module}'"
                        )
                elif node.level > 0:
                    for alias in node.names:
                        module = alias.name
                        if module == "*":
                            continue
                        if not self._resolve_python_relative_module(
                            module, node.level, file_path, existing_files
                        ):
                            relative_module = "." * node.level + module
                            errors.append(
                                f"{file_path}:{node.lineno}: Cannot resolve relative import '{relative_module}'"
                            )

        return errors

    def _is_local_python_module(self, module: str, existing_files: set[str]) -> bool:
        """Heuristic: check if a module name likely refers to local code."""
        top_package = module.split(".")[0]
        package_roots = (top_package, *(f"{root}/{top_package}" for root in _PYTHON_SOURCE_ROOTS))
        for fp in existing_files:
            if any(
                fp == root + ".py" or fp.startswith(root + "/") or fp.startswith(root + "\\")
                for root in package_roots
            ):
                return True
        return False

    def _resolve_python_module(
        self, module: str, from_file: str, existing_files: set[str]
    ) -> bool:
        """Try to resolve a Python module to an existing file."""
        # Convert module.path.name to module/path/name.py or module/path/name/__init__.py
        parts = module.split(".")
        possible_paths = [
            "/".join(parts) + ".py",
            "/".join(parts) + "/__init__.py",
        ]
        possible_paths.extend(
            f"{root}/{'/'.join(parts)}{suffix}"
            for root in _PYTHON_SOURCE_ROOTS
            for suffix in (".py", "/__init__.py")
        )
        for pp in possible_paths:
            if pp in existing_files:
                return True
        return False

    def _resolve_python_relative_module(
        self,
        module: str,
        level: int,
        from_file: str,
        existing_files: set[str],
    ) -> bool:
        """Resolve from .module / from ..module imports against project files."""
        package_parts = from_file.replace("\\", "/").split("/")[:-1]
        levels_up = level - 1
        if not package_parts or levels_up > len(package_parts):
            return False

        base_parts = package_parts[: len(package_parts) - levels_up]
        module_parts = [part for part in module.split(".") if part]
        if not module_parts:
            return False

        resolved_parts = base_parts + module_parts
        possible_paths = [
            "/".join(resolved_parts) + ".py",
            "/".join(resolved_parts) + "/__init__.py",
        ]
        return any(path in existing_files for path in possible_paths)

    # ------------------------------------------------------------------
    # JavaScript/TypeScript validation
    # ------------------------------------------------------------------

    def _validate_js_syntax(
        self, content: str, file_path: str
    ) -> tuple[bool, str | None]:
        """Basic JavaScript/TypeScript syntax validation.

        Uses heuristic checks since we can't run a full JS parser in Python.
        Checks for balanced braces, brackets, and common syntax patterns.
        """
        # Check balanced braces
        brace_count = 0
        bracket_count = 0
        paren_count = 0
        in_string = False
        string_char = ""
        in_template = False
        in_line_comment = False
        in_block_comment = False
        prev_char = ""

        for i, char in enumerate(content):
            # Handle comments
            if in_line_comment:
                if char == "\n":
                    in_line_comment = False
                continue
            if in_block_comment:
                if prev_char == "*" and char == "/":
                    in_block_comment = False
                prev_char = char
                continue

            if not in_string and not in_template:
                if prev_char == "/" and char == "/":
                    in_line_comment = True
                    prev_char = char
                    continue
                if prev_char == "/" and char == "*":
                    in_block_comment = True
                    prev_char = char
                    continue

            # Handle strings
            if not in_string and not in_template:
                if char in ('"', "'"):
                    in_string = True
                    string_char = char
                elif char == "`":
                    in_template = True
            elif in_string:
                if char == string_char and prev_char != "\\":
                    in_string = False
            elif in_template:
                if char == "`" and prev_char != "\\":
                    in_template = False

            if not in_string and not in_template:
                if char == "{":
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                elif char == "[":
                    bracket_count += 1
                elif char == "]":
                    bracket_count -= 1
                elif char == "(":
                    paren_count += 1
                elif char == ")":
                    paren_count -= 1

            prev_char = char

        errors: list[str] = []
        if brace_count != 0:
            errors.append(f"Unbalanced braces (count: {brace_count:+d})")
        if bracket_count != 0:
            errors.append(f"Unbalanced brackets (count: {bracket_count:+d})")
        if paren_count != 0:
            errors.append(f"Unbalanced parentheses (count: {paren_count:+d})")

        if errors:
            return False, "; ".join(errors)
        return True, None

    def _validate_js_imports(
        self,
        content: str,
        file_path: str,
        existing_files: set[str],
    ) -> list[str]:
        """Check JavaScript/TypeScript imports for local module resolution."""
        errors: list[str] = []

        # Match import/export module specifiers, side-effect imports, dynamic imports, and require('...')
        import_pattern = re.compile(
            r"""(?:import\s+.*?\s+from\s+['"](.+?)['"]"""
            r"""|export\s+.*?\s+from\s+['"](.+?)['"]"""
            r"""|import\s+['"](.+?)['"]"""
            r"""|import\s*\(\s*['"](.+?)['"]\s*\)"""
            r"""|require\s*\(\s*['"](.+?)['"]\s*\))"""
        )

        for i, line in enumerate(content.split("\n"), 1):
            for match in import_pattern.finditer(line):
                module_path = next((group for group in match.groups() if group), None)
                if not module_path:
                    continue

                # Only check relative imports (starts with . or ..)
                if not module_path.startswith("."):
                    continue

                resolved = self._resolve_js_import(module_path, file_path, existing_files)
                if not resolved:
                    errors.append(
                        f"{file_path}:{i}: Cannot resolve import '{module_path}'"
                    )

        return errors

    def _resolve_js_import(
        self,
        module_path: str,
        from_file: str,
        existing_files: set[str],
    ) -> bool:
        """Try to resolve a relative JS/TS import to an existing file."""
        # Compute the base directory of the importing file
        from_dir = "/".join(from_file.replace("\\", "/").split("/")[:-1])
        if from_dir:
            resolved_base = from_dir + "/" + module_path
        else:
            resolved_base = module_path

        # Normalize path (handle ./ and ../)
        parts = resolved_base.split("/")
        normalized: list[str] = []
        for part in parts:
            if part in ("", "."):
                continue
            elif part == "..":
                if not normalized:
                    return False
                normalized.pop()
            else:
                normalized.append(part)
        resolved_base = "/".join(normalized)

        # Try various extensions and index files
        candidates = [
            resolved_base,
            resolved_base + ".ts",
            resolved_base + ".tsx",
            resolved_base + ".js",
            resolved_base + ".jsx",
            resolved_base + ".mjs",
            resolved_base + "/index.ts",
            resolved_base + "/index.tsx",
            resolved_base + "/index.js",
            resolved_base + "/index.jsx",
            resolved_base + "/index.mjs",
        ]

        for candidate in candidates:
            if candidate in existing_files:
                return True

        return False

    # ------------------------------------------------------------------
    # JSON / YAML syntax
    # ------------------------------------------------------------------

    def _validate_json_syntax(
        self, content: str, file_path: str
    ) -> tuple[bool, str | None]:
        """Validate JSON syntax."""
        import json

        try:
            json.loads(content)
            return True, None
        except json.JSONDecodeError as e:
            return False, f"JSON error at line {e.lineno}: {e.msg}"

    def _validate_yaml_syntax(
        self, content: str, file_path: str
    ) -> tuple[bool, str | None]:
        """Validate YAML syntax if PyYAML is available."""
        try:
            import yaml

            yaml.safe_load(content)
            return True, None
        except ImportError:
            return True, None  # Can't validate without PyYAML
        except yaml.YAMLError as e:
            return False, f"YAML error: {e}"

    # ------------------------------------------------------------------
    # External tool runners
    # ------------------------------------------------------------------

    async def _kill_timed_out_process(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return

        try:
            proc.kill()
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning(
                "Failed to kill timed-out validation process: %s",
                _redact_validation_error(exc),
            )
            return

        try:
            await asyncio.wait_for(
                proc.communicate(), timeout=_VALIDATION_DRAIN_TIMEOUT_SECONDS
            )
        except Exception as exc:
            logger.warning(
                "Failed to drain timed-out validation process: %s",
                _redact_validation_error(exc),
            )

    async def _run_pytest(self, project_dir: str) -> dict:
        """Run pytest in the project directory and parse results."""
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pytest",
                "--tb=short",
                "-q",
                "--no-header",
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except FileNotFoundError as e:
            safe_error = _redact_validation_error(e)
            logger.warning("pytest execution failed: %s", safe_error)
            return {"passed": 0, "failed": 0, "errors": [safe_error]}
        except OSError as e:
            safe_error = _redact_validation_error(e)
            message = f"failed to start pytest: {safe_error}"
            logger.warning("pytest execution failed: %s", safe_error)
            return {"passed": 0, "failed": 0, "errors": [message]}

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=60.0
            )
        except asyncio.TimeoutError:
            await self._kill_timed_out_process(proc)
            message = "pytest timed out after 60s"
            logger.warning("pytest execution failed: %s", message)
            return {"passed": 0, "failed": 0, "errors": [message]}

        output = stdout.decode("utf-8", errors="replace")
        errors: list[str] = []

        # Parse pytest summary line: "5 passed, 2 failed"
        passed = 0
        failed = 0
        summary_match = re.search(r"(\d+) passed", output)
        if summary_match:
            passed = int(summary_match.group(1))
        fail_match = re.search(r"(\d+) failed", output)
        if fail_match:
            failed = int(fail_match.group(1))
        error_match = re.search(r"(\d+) error", output)
        if error_match:
            errors.append(f"{error_match.group(1)} collection errors")

        if proc.returncode == 1 and failed == 0 and not errors:
            stderr_text = stderr.decode("utf-8", errors="replace")
            fallback_error = stderr_text.strip() or output.strip()
            if fallback_error:
                errors.append(_redact_validation_error(fallback_error)[:500])
        elif proc.returncode not in (0, 1, 5):  # 5 = no tests collected
            stderr_text = stderr.decode("utf-8", errors="replace")
            fallback_error = stderr_text.strip() or output.strip()
            if fallback_error:
                errors.append(_redact_validation_error(fallback_error)[:500])

        return {"passed": passed, "failed": failed, "errors": errors}

    async def _run_tsc(self, project_dir: str) -> list[str]:
        """Run TypeScript compiler in noEmit mode and collect errors."""
        tsconfig = Path(project_dir) / "tsconfig.json"
        if not tsconfig.exists():
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "--no-install", "tsc", "--noEmit", "--pretty", "false",
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            safe_error = _redact_validation_error(e)
            logger.warning("tsc execution failed: %s", safe_error)
            return [safe_error]
        except OSError as e:
            safe_error = _redact_validation_error(e)
            message = f"failed to start tsc: {safe_error}"
            logger.warning("tsc execution failed: %s", safe_error)
            return [message]

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=90.0
            )
        except asyncio.TimeoutError:
            await self._kill_timed_out_process(proc)
            message = "tsc timed out after 90s"
            logger.warning("tsc execution failed: %s", message)
            return [message]

        if proc.returncode == 0:
            return []

        output = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        errors: list[str] = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if line and "error TS" in line:
                errors.append(_redact_validation_error(line))

        if not errors:
            fallback_error = stderr_text.strip() or output.strip()
            if fallback_error:
                errors.append(_redact_validation_error(fallback_error)[:500])

        return errors[:20]  # Cap at 20 errors
