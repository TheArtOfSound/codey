"""Closed-loop repo executor — the layer that makes a patch real and proven.

Pipeline (for connected-repo / autopilot runs):

    clone (commitBefore) -> apply writer patch -> REAL git diff
      -> verify claims against the real diff -> run validation commands
      -> commit (commitAfter) -> governor scores reality -> receipt

The diff, commits, and command exit codes come from git and the shell — not the
model — so the resulting status cannot be faked.  Syntax checks (``node --check``,
``py_compile``) are safe (no code execution) and run by default; project commands
(``npm test``/``build``/``lint``) execute code and are gated behind
``allow_project_commands`` (intended for the E2B sandbox, not the shared host).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Optional

from codey.saas.sessions.patch_receipt import (
    ClaimVerificationResult,
    CommandRun,
    FileChange,
    PatchReceipt,
    RunIntent,
    RunStatus,
    Validation,
    coarse_status,
    extract_claims,
    verify_patch_claims,
)
from codey.saas.sessions.run_governor import (
    RunGovernorContext,
    build_lolm_receipt,
    compute_control_signals,
    govern_completion,
    nfet_field_energy,
    select_actions,
)

_TAIL = 4000


def _run(args: list[str], cwd: str, timeout: int = 120, env: Optional[dict] = None) -> CommandRun:
    try:
        proc = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **(env or {})},
        )
        return CommandRun(
            command=" ".join(args),
            exitCode=proc.returncode,
            passed=proc.returncode == 0,
            stdoutTail=(proc.stdout or "")[-_TAIL:],
            stderrTail=(proc.stderr or "")[-_TAIL:],
        )
    except subprocess.TimeoutExpired:
        return CommandRun(command=" ".join(args), exitCode=124, passed=False,
                          stderrTail=f"timeout after {timeout}s")
    except FileNotFoundError as exc:
        return CommandRun(command=" ".join(args), exitCode=127, passed=False,
                          stderrTail=f"command not found: {exc}")


def _git_out(args: list[str], cwd: str, timeout: int = 120) -> str:
    """Run a git command and return its FULL, untruncated stdout.

    Unlike :func:`_run`, this never tail-truncates. Callers parse this output
    for control flow (file lists, numstat, status, diffs); dropping the head —
    as the 4 KB ``_TAIL`` cap did — silently corrupts the parsed result and
    makes good commits look like "nothing to commit" / failed verification.
    """
    try:
        proc = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            env={**os.environ},
        )
        return proc.stdout or ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


class RepoExecutor:
    """A real working tree backed by git in a temp/clone dir."""

    def __init__(self, workdir: str) -> None:
        self.workdir = workdir

    # -- construction --
    @classmethod
    def init_local(cls, workdir: str) -> "RepoExecutor":
        ex = cls(workdir)
        _run(["git", "init", "-q"], workdir)
        _run(["git", "config", "user.email", "codey@imagineqira.com"], workdir)
        _run(["git", "config", "user.name", "Codey"], workdir)
        return ex

    @classmethod
    def clone(cls, clone_url: str, dest: str, *, branch: Optional[str] = None,
              token: Optional[str] = None, timeout: int = 120) -> "RepoExecutor":
        url = clone_url
        if token and url.startswith("https://"):
            url = url.replace("https://", f"https://x-access-token:{token}@", 1)
        args = ["git", "clone", "--depth", "1"]
        if branch:
            args += ["--branch", branch]
        args += [url, dest]
        res = _run(args, cwd=os.path.dirname(dest) or ".", timeout=timeout)
        if not res.passed:
            raise RuntimeError(f"clone failed: {res.stderrTail}")
        ex = cls(dest)
        _run(["git", "config", "user.email", "codey@imagineqira.com"], dest)
        _run(["git", "config", "user.name", "Codey"], dest)
        return ex

    # -- git ops --
    def head(self) -> Optional[str]:
        res = _run(["git", "rev-parse", "HEAD"], self.workdir)
        return (res.stdoutTail or "").strip() or None if res.passed else None

    def write_files(self, files: dict[str, str]) -> list[str]:
        written = []
        for rel, content in files.items():
            safe = os.path.normpath(rel).lstrip("/")
            if safe.startswith(".."):
                continue  # never escape the workdir
            full = os.path.join(self.workdir, safe)
            os.makedirs(os.path.dirname(full) or self.workdir, exist_ok=True)
            with open(full, "w") as fh:
                fh.write(content)
            written.append(safe)
        return written

    def apply_unified_diff(self, diff_text: str) -> CommandRun:
        path = os.path.join(self.workdir, ".codey_patch.diff")
        with open(path, "w") as fh:
            fh.write(diff_text)
        res = _run(["git", "apply", "--whitespace=nowarn", ".codey_patch.diff"], self.workdir)
        try:
            os.unlink(path)
        except OSError:
            pass
        return res

    def real_diff(self) -> tuple[str, list[FileChange], str]:
        """The REAL git diff of the working tree vs HEAD (staged for accuracy)."""
        _run(["git", "add", "-A"], self.workdir)
        text = _git_out(["git", "diff", "--cached"], self.workdir, timeout=60)[:200_000]
        numstat = _git_out(["git", "diff", "--cached", "--numstat"], self.workdir)
        namestat = _git_out(["git", "diff", "--cached", "--name-status"], self.workdir)
        kinds = {}
        for line in namestat.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                code = parts[0][0]
                kinds[parts[-1]] = {"A": "created", "M": "modified", "D": "deleted",
                                    "R": "renamed"}.get(code, "modified")
        changes: list[FileChange] = []
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                adds = 0 if parts[0] == "-" else int(parts[0] or 0)
                dels = 0 if parts[1] == "-" else int(parts[1] or 0)
                path = parts[2]
                changes.append(FileChange(path=path, additions=adds, deletions=dels,
                                          changeKind=kinds.get(path, "modified")))
        diff_hash = hashlib.sha256(text.encode("utf-8")).hexdigest() if text.strip() else ""
        return text, changes, diff_hash

    def commit(self, message: str) -> Optional[str]:
        _run(["git", "add", "-A"], self.workdir)
        # Nothing staged -> no commit.
        status = _git_out(["git", "status", "--porcelain"], self.workdir)
        if not status.strip():
            return self.head()
        _run(["git", "commit", "-q", "-m", message], self.workdir)
        return self.head()


class ValidationRunner:
    """Runs real validation commands and reports honestly what ran."""

    @staticmethod
    def run(workdir: str, changed_paths: list[str], *,
            allow_project_commands: bool = True,
            install_timeout: int = 600,
            cmd_timeout: int = 600) -> tuple[Validation, list[CommandRun]]:
        """Run real validation. Project commands (npm/pytest) execute code, so
        they run on connected (owned) repos; for untrusted multi-tenant repos
        wrap this in the E2B sandbox. Reports honestly what ran (None == not run).
        """
        v = Validation()
        cmds: list[CommandRun] = []

        # 1) Safe syntax checks (no code execution) — always.
        js = [p for p in changed_paths if p.endswith((".js", ".mjs", ".cjs"))]
        py = [p for p in changed_paths if p.endswith(".py")]
        for f in js:
            cmds.append(_run(["node", "--check", f], workdir, timeout=30))
        for f in py:
            cmds.append(_run(["python3", "-m", "py_compile", f], workdir, timeout=30))
        syntax_cmds = [c for c in cmds if "--check" in c.command or "py_compile" in c.command]
        if syntax_cmds:
            v.syntaxChecked = all(c.passed for c in syntax_cmds)

        if not allow_project_commands:
            return v, cmds

        # 2) Real project commands.
        import json
        pkg = os.path.join(workdir, "package.json")
        if os.path.exists(pkg):
            try:
                manifest = json.load(open(pkg))
            except Exception:
                manifest = {}
            scripts = manifest.get("scripts") or {}
            deps = {**(manifest.get("dependencies") or {}), **(manifest.get("devDependencies") or {})}
            # Install deps so tests/build run for real (only when there are deps
            # and they're not already installed).
            if deps and not os.path.isdir(os.path.join(workdir, "node_modules")):
                inst = _run(["npm", "ci", "--no-audit", "--no-fund"], workdir, timeout=install_timeout)
                if not inst.passed:
                    inst = _run(["npm", "install", "--no-audit", "--no-fund"], workdir, timeout=install_timeout)
                cmds.append(inst)
            for script, setter in (
                ("lint", "lintPassed"),
                ("typecheck", "typecheckPassed"),
                ("test", "testsPassed"),
                ("build", "buildPassed"),
            ):
                if script in scripts:
                    c = _run(["npm", "run", script, "--silent"], workdir, timeout=cmd_timeout)
                    cmds.append(c)
                    setattr(v, setter, c.passed)

        # Python projects.
        is_py_project = any(
            os.path.exists(os.path.join(workdir, f))
            for f in ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
        ) or any(("test" in p and p.endswith(".py")) for p in changed_paths)
        if is_py_project:
            c = _run(["python3", "-m", "pytest", "-q"], workdir, timeout=cmd_timeout)
            cmds.append(c)
            v.testsPassed = c.passed if v.testsPassed is None else (v.testsPassed and c.passed)

        return v, cmds


@dataclass
class ExecutedRun:
    status: RunStatus
    receipt: dict
    diff_text: str
    file_changes: list[FileChange]
    e_code: float
    validation: Validation


def apply_and_verify(
    executor: RepoExecutor,
    *,
    files: Optional[dict[str, str]] = None,
    diff_text: Optional[str] = None,
    explanation: str,
    intent: RunIntent = RunIntent.APPLY_PATCH,
    writer_model: str = "writer-llm",
    repo: Optional[dict] = None,
    files_read: Optional[list[str]] = None,
    allow_project_commands: bool = True,
    es_before: Optional[float] = None,
    es_after: Optional[float] = None,
    commit_message: Optional[str] = None,
) -> ExecutedRun:
    """Apply a writer patch to a real working tree and prove the result."""
    commit_before = executor.head()

    if files:
        executor.write_files(files)
    if diff_text:
        executor.apply_unified_diff(diff_text)

    real_diff, file_changes, diff_hash = executor.real_diff()
    files_modified = len(file_changes)
    # Claims are checked against the REAL diff (removed lines) AND the resulting
    # on-disk file content (so "removed X" fails if X is still in the new file).
    result_parts: list[str] = []
    for c in file_changes:
        fp = os.path.join(executor.workdir, c.path)
        if os.path.isfile(fp):
            try:
                result_parts.append(open(fp).read())
            except OSError:
                pass
    result_content = "\n".join(result_parts)

    claims = extract_claims(explanation)
    verification: ClaimVerificationResult = verify_patch_claims(
        claims, real_diff, [c.path for c in file_changes], result_content=result_content,
    )

    validation, cmds = ValidationRunner.run(
        executor.workdir, [c.path for c in file_changes],
        allow_project_commands=allow_project_commands,
    )
    validation.claimVerificationPassed = verification.passed
    validation.patchApplied = files_modified > 0
    validation.filesModifiedCount = files_modified
    syntax_failed = any(
        (("--check" in c.command) or ("py_compile" in c.command)) and not c.passed
        for c in cmds
    )
    # A correctness failure (syntax / test / build / typecheck) blocks both the
    # commit and a successful status. Lint is advisory.
    validation_failed = (
        syntax_failed
        or validation.testsPassed is False
        or validation.buildPassed is False
        or validation.typecheckPassed is False
    )

    # Only commit a patch that changed files, whose claims match the diff, and
    # whose changed sources parse + pass validation.
    commit_after = None
    if files_modified > 0 and verification.passed and not validation_failed:
        commit_after = executor.commit(commit_message or "Codey: apply verified patch")

    gov_ctx = RunGovernorContext(
        intent=intent, prompt=explanation, files_read=files_read or [],
        file_changes=file_changes, diff_text=real_diff,
        verification_passed=verification.passed,
        claims_total=len(verification.checks),
        claims_mismatched=len(verification.mismatches),
        validation=validation, patch_applied=files_modified > 0,
        es_before=es_before, es_after=es_after,
    )
    signals = compute_control_signals(gov_ctx)
    e_code = nfet_field_energy(signals)
    actions = select_actions(signals, e_code, intent=intent,
                             files_modified=files_modified, patch_applied=files_modified > 0)

    # Base status from reality, then governor gate.
    tests_failed = validation_failed
    from codey.saas.sessions.patch_receipt import derive_run_status
    base = derive_run_status(
        intent, files_modified, patch_applied=files_modified > 0,
        claim_verification_passed=verification.passed, tests_failed=tests_failed,
    )
    status, reason = govern_completion(base, signals, e_code)

    receipt = PatchReceipt(
        receiptId=hashlib.sha256((diff_hash + (commit_before or "")).encode()).hexdigest()[:16],
        runId=(repo or {}).get("runId", "exec-run"),
        repoId=(repo or {}).get("name"),
        intent=intent, status=status, startedAt="", completedAt="",
        branch=(repo or {}).get("branch"),
        commitBefore=commit_before, commitAfter=commit_after,
        filesRead=files_read or [], filesChanged=file_changes,
        diffText=real_diff, diffHash=diff_hash,
        claimsMade=verification.checks, commandsRun=cmds, validation=validation,
        phases=[
            {"phase": "apply_patch", "ok": files_modified > 0},
            {"phase": "compute_diff", "ok": bool(diff_hash)},
            {"phase": "verify_claims", "ok": verification.passed},
            {"phase": "run_validation", "ok": validation.syntaxChecked},
            {"phase": "commit", "ok": commit_after is not None and commit_after != commit_before},
        ],
        healthBefore=es_before, healthAfter=es_after,
        finalSummary=explanation[:2000],
    )
    lolm = build_lolm_receipt(
        run_id=receipt.runId, writer_model=writer_model,
        repo={**(repo or {}), "commitBefore": commit_before, "commitAfter": commit_after},
        signals=signals, e_code=e_code, actions=actions, reason=reason,
        files_read=files_read or [], file_changes=file_changes, diff_hash=diff_hash,
        patch_applied=files_modified > 0, verification=verification,
        validation=validation, final_status=status,
    )
    doc = receipt.to_dict()
    doc["control"] = lolm.control
    doc["lolm"] = lolm.to_dict()
    return ExecutedRun(status=status, receipt=doc, diff_text=real_diff,
                       file_changes=file_changes, e_code=e_code, validation=validation)


def branch_and_pick(
    baseline_dir: str,
    candidates: list[dict],
    *,
    explanation_for: Callable[[dict], str],
    files_for: Callable[[dict], dict[str, str]],
    allow_project_commands: bool = True,
) -> tuple[Optional[ExecutedRun], list[dict]]:
    """Run N candidate patches in isolated copies and pick the best.

    Best = passes validation + claims, lowest E_code.  Returns the winning
    ExecutedRun and a summary of every candidate for the receipt.
    """
    results: list[tuple[dict, ExecutedRun]] = []
    summaries: list[dict] = []
    for i, cand in enumerate(candidates):
        wd = tempfile.mkdtemp(prefix=f"codey-branch-{i}-")
        try:
            shutil.copytree(baseline_dir, wd, dirs_exist_ok=True)
            ex = RepoExecutor(wd)
            run = apply_and_verify(
                ex, files=files_for(cand), explanation=explanation_for(cand),
                allow_project_commands=allow_project_commands,
            )
            results.append((cand, run))
            summaries.append({
                "candidate": i, "status": run.status.value, "e_code": run.e_code,
                "files": len(run.file_changes),
                "claims_ok": run.validation.claimVerificationPassed,
                "syntax_ok": run.validation.syntaxChecked,
            })
        finally:
            shutil.rmtree(wd, ignore_errors=True)

    passing = [(c, r) for c, r in results if r.status in (
        RunStatus.COMPLETED_WITH_PATCH,) and r.validation.claimVerificationPassed]
    pool = passing or results
    if not pool:
        return None, summaries
    winner = min(pool, key=lambda cr: cr[1].e_code)
    return winner[1], summaries
