"""Patch receipts and claim verification for Codey runs.

This module is the trust layer that makes Codey a *coding-agent platform*
rather than a text-output wrapper.  A modifying run can only be reported as
successfully completed when the repository state, the diff, the stated claims,
and the receipt all agree.

Everything here is pure (dataclasses + functions, no I/O), so it is trivial to
unit-test and safe to import from the runtime run-completion path.

Pipeline this supports:

    brief -> run intent -> repo patch -> git diff -> claim verifier
          -> validation commands -> patch receipt -> final run status
          -> dashboard render
"""

from __future__ import annotations

import dataclasses
import difflib
import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Run intent + status
# ---------------------------------------------------------------------------

class RunIntent(str, Enum):
    READ_ONLY_REVIEW = "read_only_review"
    PROPOSED_PATCH = "proposed_patch"
    APPLY_PATCH = "apply_patch"
    AUTOPILOT_FIX = "autopilot_fix"
    REPO_SCAN = "repo_scan"
    TEST_ONLY = "test_only"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED_WITH_PATCH = "completed_with_patch"
    COMPLETED_NO_CHANGES = "completed_no_changes"
    COMPLETED_READ_ONLY = "completed_read_only"
    FAILED_PATCH_NOT_APPLIED = "failed_patch_not_applied"
    FAILED_VERIFICATION = "failed_verification"
    FAILED_TESTS = "failed_tests"
    FAILED_RUNTIME = "failed_runtime"
    CANCELLED = "cancelled"


# Intents that are *supposed* to change the repository.
MODIFYING_INTENTS = frozenset({RunIntent.APPLY_PATCH, RunIntent.AUTOPILOT_FIX})

# Statuses that count as a non-failed terminal run.
SUCCESS_STATUSES = frozenset({
    RunStatus.COMPLETED_WITH_PATCH,
    RunStatus.COMPLETED_NO_CHANGES,
    RunStatus.COMPLETED_READ_ONLY,
})

# Map precise RunStatus -> the coarse legacy CodingSession.status bucket so we
# never break existing consumers that test for "completed"/"failed"/"running".
_COARSE_STATUS = {
    RunStatus.QUEUED: "queued",
    RunStatus.RUNNING: "running",
    RunStatus.COMPLETED_WITH_PATCH: "completed",
    RunStatus.COMPLETED_NO_CHANGES: "completed",
    RunStatus.COMPLETED_READ_ONLY: "completed",
    RunStatus.FAILED_PATCH_NOT_APPLIED: "failed",
    RunStatus.FAILED_VERIFICATION: "failed",
    RunStatus.FAILED_TESTS: "failed",
    RunStatus.FAILED_RUNTIME: "failed",
    RunStatus.CANCELLED: "cancelled",
}


def coarse_status(status: RunStatus) -> str:
    return _COARSE_STATUS.get(status, "failed")


def default_intent_for_brief(text: str) -> RunIntent:
    """A brief that asks Codey to *fix*/*change*/*apply* defaults to apply_patch.

    "If the user asks Codey to fix a file, default to apply_patch, not
    read-only suggestion mode."
    """
    t = (text or "").lower()
    if any(k in t for k in (
        "fix", "apply", "patch", "modify", "change", "refactor", "implement",
        "remove", "delete", "rename", "update", "repair", "resolve",
    )):
        return RunIntent.APPLY_PATCH
    if any(k in t for k in ("review", "audit", "explain", "suggest", "propose")):
        return RunIntent.PROPOSED_PATCH
    return RunIntent.APPLY_PATCH


# ---------------------------------------------------------------------------
# Receipt sub-structures
# ---------------------------------------------------------------------------

@dataclass
class FileChange:
    path: str
    additions: int = 0
    deletions: int = 0
    changeKind: str = "modified"  # created | modified | deleted | renamed


@dataclass
class ClaimCheck:
    claim: str
    sourceSection: str = "summary"
    matchedByDiff: bool = False
    checkable: bool = True
    evidence: Optional[str] = None
    mismatchReason: Optional[str] = None


@dataclass
class CommandRun:
    command: str
    exitCode: int = 0
    passed: bool = False
    stdoutTail: Optional[str] = None
    stderrTail: Optional[str] = None


@dataclass
class Validation:
    syntaxChecked: bool = False
    typecheckPassed: Optional[bool] = None
    lintPassed: Optional[bool] = None
    testsPassed: Optional[bool] = None
    buildPassed: Optional[bool] = None
    smokeTestPassed: Optional[bool] = None
    claimVerificationPassed: bool = False
    patchApplied: bool = False
    filesModifiedCount: int = 0


@dataclass
class PatchReceipt:
    receiptId: str
    runId: str
    repoId: Optional[str]
    intent: RunIntent
    status: RunStatus
    startedAt: str
    completedAt: Optional[str] = None
    branch: Optional[str] = None
    commitBefore: Optional[str] = None
    commitAfter: Optional[str] = None
    filesRead: list[str] = field(default_factory=list)
    filesChanged: list[FileChange] = field(default_factory=list)
    diffText: str = ""
    diffHash: str = ""
    claimsMade: list[ClaimCheck] = field(default_factory=list)
    commandsRun: list[CommandRun] = field(default_factory=list)
    validation: Validation = field(default_factory=Validation)
    phases: list[dict] = field(default_factory=list)
    healthBefore: Optional[float] = None
    healthAfter: Optional[float] = None
    healthScore: float = 0.0
    finalSummary: str = ""

    def to_dict(self) -> dict:
        """JSON-serializable form (enums -> values) for JSONB persistence."""
        def _coerce(value):
            if isinstance(value, Enum):
                return value.value
            if dataclasses.is_dataclass(value):
                return {k: _coerce(v) for k, v in dataclasses.asdict(value).items()}
            if isinstance(value, list):
                return [_coerce(v) for v in value]
            if isinstance(value, dict):
                return {k: _coerce(v) for k, v in value.items()}
            return value

        return {k: _coerce(v) for k, v in dataclasses.asdict(self).items()}


@dataclass
class ClaimVerificationResult:
    passed: bool
    checks: list[ClaimCheck] = field(default_factory=list)

    @property
    def mismatches(self) -> list[ClaimCheck]:
        return [c for c in self.checks if c.checkable and not c.matchedByDiff]


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def compute_diff(
    originals: dict[str, str],
    generated: dict[str, str],
) -> tuple[str, list[FileChange], str]:
    """Compute a unified diff for original -> generated content.

    Returns ``(diff_text, [FileChange], diff_hash)``.  Files present only in
    ``generated`` are "created"; files whose content is unchanged are omitted
    from ``filesChanged`` (this is what makes "Files Modified: 0" honest).
    """
    diff_chunks: list[str] = []
    changes: list[FileChange] = []
    for path in sorted(generated.keys()):
        new = generated.get(path, "") or ""
        old = originals.get(path, "") or ""
        if new == old:
            continue  # genuinely no change -> not a "changed file"
        kind = "created" if path not in originals else "modified"
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)
        udiff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{path}", tofile=f"b/{path}", n=3,
        ))
        additions = sum(1 for line in udiff if line.startswith("+") and not line.startswith("+++"))
        deletions = sum(1 for line in udiff if line.startswith("-") and not line.startswith("---"))
        diff_chunks.append("".join(udiff))
        changes.append(FileChange(
            path=path, additions=additions, deletions=deletions, changeKind=kind,
        ))
    # Deletions: present in originals, absent (or emptied) in generated.
    for path in sorted(originals.keys()):
        if path not in generated:
            changes.append(FileChange(
                path=path, additions=0,
                deletions=len((originals.get(path, "") or "").splitlines()),
                changeKind="deleted",
            ))
    diff_text = "\n".join(c for c in diff_chunks if c)
    diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest() if diff_text else ""
    return diff_text, changes, diff_hash


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

# Verbs that introduce a concrete, checkable assertion about a code change.
_CLAIM_VERBS = (
    "remov", "delet", "drop", "strip", "eliminat",       # removals
    "add", "introduc", "insert", "import",                # additions
    "replac", "rename", "renam", "rewrit", "refactor",   # transforms
    "fix", "updat", "chang", "modif", "implement",        # generic edits
    "open",                                               # the "Opening" trap
)


def extract_claims(text: str) -> list[str]:
    """Pull concrete change-claims out of an LLM explanation.

    Splits on sentence/line/bullet boundaries and keeps fragments that assert a
    code change (contain a change-verb).  Vague prose ("improves readability")
    is dropped so it neither passes nor fails verification spuriously.
    """
    if not text:
        return []
    # Normalise bullets/markdown to sentence-ish fragments.
    raw = re.split(r"(?:\r?\n)+|(?<=[.;])\s+|(?:^|\n)\s*[-*•]\s+", text)
    claims: list[str] = []
    for frag in raw:
        frag = frag.strip().lstrip("-*• ").strip()
        if len(frag) < 4:
            continue
        low = frag.lower()
        if any(v in low for v in _CLAIM_VERBS):
            claims.append(frag[:280])
    return claims


# ---------------------------------------------------------------------------
# Claim verification
# ---------------------------------------------------------------------------

_OPEN_ACTION_PATTERNS = (
    "openfile", "open_file", "spawn", "execfile", "child_process", "execa",
    "xdg-open", "os.startfile", "startfile", "shell.openitem", "shell.openpath",
    "exec(", "execsync", "open(", "subprocess", "start \"\"", "/c start",
    "powershell start", "explorer ", "/usr/bin/open", "\"open\"", "'open'",
)


def _removed_lines(diff_text: str) -> str:
    return "\n".join(
        l[1:] for l in diff_text.splitlines()
        if l.startswith("-") and not l.startswith("---")
    ).lower()


def _added_lines(diff_text: str) -> str:
    return "\n".join(
        l[1:] for l in diff_text.splitlines()
        if l.startswith("+") and not l.startswith("+++")
    ).lower()


def _claim_targets(claim: str) -> list[str]:
    """Heuristic 'subject' tokens of a claim (what was acted on)."""
    low = claim.lower()
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_./-]{2,}", low)
    stop = {
        "the", "and", "for", "that", "this", "with", "from", "into", "was",
        "were", "has", "have", "been", "removed", "remove", "removes",
        "deleted", "delete", "added", "add", "fixed", "fix", "updated",
        "update", "changed", "change", "clause", "statement", "import",
        "imports", "unused", "now", "instead", "longer", "code", "file",
        "function", "method", "call", "block", "logic", "issue", "structural",
        "downloaded", "installer", "message", "behavior", "behaviour",
    }
    return [t for t in toks if t not in stop]


_CODE_EXTS = {
    "ts", "tsx", "js", "jsx", "mjs", "cjs", "py", "md", "json", "css", "scss",
    "sass", "go", "rs", "java", "rb", "php", "sh", "bash", "yml", "yaml",
    "toml", "html", "txt", "sql", "c", "cpp", "h", "hpp", "kt", "swift",
    "vue", "svelte", "ini", "cfg",
}


def _claim_file_paths(claim: str) -> list[str]:
    """File paths a claim explicitly names (filtered to code/doc extensions)."""
    out = []
    for cand in re.findall(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]{1,6}", claim):
        if cand.rsplit(".", 1)[-1].lower() in _CODE_EXTS:
            out.append(cand)
    return out


def verify_patch_claims(
    claims: list[str],
    diff_text: str,
    files_changed: list[str],
    result_content: str = "",
) -> ClaimVerificationResult:
    """Compare each stated claim against the actual diff / resulting content.

    A claim *fails* only when it makes a concrete, checkable assertion whose
    evidence is absent from the diff/result.  Unclassifiable prose is marked
    ``checkable=False`` and does not affect the pass/fail outcome.
    """
    removed = _removed_lines(diff_text)
    added = _added_lines(diff_text)
    result_low = (result_content or "").lower()
    files_low = [f.lower() for f in files_changed]
    checks: list[ClaimCheck] = []

    for claim in claims:
        low = claim.lower()
        chk = ClaimCheck(claim=claim)

        # 0) File-path precedence (strongest, least-spoofable signal): any source
        #    file a claim names as changed MUST be in the changed set. This runs
        #    before the verb heuristics so "updated/removed ... in foo.ts" cannot
        #    pass when foo.ts was never touched.
        named_paths = _claim_file_paths(claim)
        if named_paths:
            missing = [
                p for p in named_paths
                if not any(
                    f == p.lower() or f.endswith(p.lower()) or p.lower().endswith(f)
                    for f in files_low
                )
            ]
            if missing:
                chk.matchedByDiff = False
                chk.mismatchReason = (
                    f"Claim references {missing}, but those files are not in the changed set."
                )
                checks.append(chk)
                continue

        # 1) "opened the file" / "Opening" -> result must contain an open action.
        mentions_open = ("open" in low) or ("launch" in low)
        if mentions_open and not any(w in low for w in ("opener", "opening hours")):
            has_open = any(p in result_low or p in added for p in _OPEN_ACTION_PATTERNS)
            chk.matchedByDiff = has_open
            chk.evidence = "open/launch action present in code" if has_open else None
            if not has_open:
                chk.mismatchReason = (
                    "Claim says the file is opened/launched, but no open action "
                    "(openFile/spawn/open/xdg-open/os.startfile/...) is present."
                )
            checks.append(chk)
            continue

        # 2) Removal claims -> a matching deletion must exist in the diff.
        if any(v in low for v in ("remov", "delet", "drop", "strip", "eliminat")):
            targets = _claim_targets(claim)
            # "else clause" / "else statement"
            if "else" in low:
                pat = r"(^|[^A-Za-z])else([^A-Za-z]|$)"
                still_present = bool(re.search(pat, result_low)) if result_low else None
                if still_present:
                    chk.matchedByDiff = False
                    chk.mismatchReason = (
                        "Claim removes an `else` clause, but `else` is still present "
                        "in the resulting code."
                    )
                else:
                    hit = bool(re.search(pat, removed)) or still_present is False
                    chk.matchedByDiff = hit
                    chk.evidence = "`else` removed (diff/result)" if hit else None
                    if not hit:
                        chk.mismatchReason = "No evidence the `else` clause was removed."
                checks.append(chk)
                continue
            if "import" in low or "require" in low:
                # e.g. "removed the console import"
                subj = next((t for t in targets if t not in ("require", "imports")), None)
                still_present = None
                if result_low:
                    if subj:
                        still_present = bool(
                            re.search(r"(import|require)[^\n]*" + re.escape(subj), result_low)
                        )
                    else:
                        still_present = ("import" in result_low or "require" in result_low)
                if still_present:
                    chk.matchedByDiff = False
                    chk.mismatchReason = (
                        f"Claim removes the `{subj or 'named'}` import, but it is still "
                        "present in the resulting code."
                    )
                else:
                    diff_hit = ("import" in removed or "require" in removed) and (
                        subj is None or subj in removed
                    )
                    chk.matchedByDiff = diff_hit or still_present is False
                    chk.evidence = "import/require removed (diff/result)" if chk.matchedByDiff else None
                    if not chk.matchedByDiff:
                        chk.mismatchReason = (
                            f"No evidence the `{subj or 'named'}` import was removed."
                        )
                checks.append(chk)
                continue
            if targets:
                hit = any(t in removed for t in targets)
                chk.matchedByDiff = hit
                chk.evidence = "removed token present in diff" if hit else None
                if not hit:
                    chk.mismatchReason = (
                        f"Claim removes {targets[:3]}, but none of those tokens were deleted in the diff."
                    )
                checks.append(chk)
                continue
            # Generic removal with no clear subject + empty diff -> fail.
            chk.matchedByDiff = bool(removed.strip())
            if not chk.matchedByDiff:
                chk.mismatchReason = "Claim describes a removal, but the diff deletes nothing."
            checks.append(chk)
            continue

        # 3) Addition / import-added claims -> a matching addition must exist.
        if any(v in low for v in ("add", "introduc", "insert")) or (
            "import" in low and "remov" not in low
        ):
            targets = _claim_targets(claim)
            hit = any(t in added for t in targets) if targets else bool(added.strip())
            chk.matchedByDiff = hit
            chk.evidence = "added token present in diff" if hit else None
            if not hit:
                chk.mismatchReason = "Claim adds code, but no matching addition is in the diff."
            checks.append(chk)
            continue

        # 4) "updated/modified/fixed <path>" -> path must be in files_changed.
        path_in_claim = re.findall(r"[\w./-]+\.[A-Za-z0-9]{1,5}", claim)
        if path_in_claim and any(v in low for v in ("updat", "modif", "fix", "chang", "patch", "edit")):
            ok = any(
                any(p.lower() in fc or fc.endswith(p.lower()) for fc in files_low)
                for p in path_in_claim
            )
            chk.matchedByDiff = ok
            chk.evidence = "target file is in filesChanged" if ok else None
            if not ok:
                chk.mismatchReason = (
                    f"Claim says {path_in_claim} was changed, but it is not in the set of changed files."
                )
            checks.append(chk)
            continue

        # 5) Generic edit claims with no concrete subject: checkable only if the
        #    diff is empty (then they are false), otherwise treat as prose.
        if any(v in low for v in ("fix", "updat", "chang", "modif", "implement", "replac", "rename", "refactor")):
            if not (added.strip() or removed.strip()):
                chk.matchedByDiff = False
                chk.mismatchReason = "Claim asserts a code change, but the diff is empty."
            else:
                chk.checkable = False  # plausible but not pinpointable
                chk.matchedByDiff = True
            checks.append(chk)
            continue

        # Everything else: prose, not a checkable assertion.
        chk.checkable = False
        chk.matchedByDiff = True
        checks.append(chk)

    # Code-promise check: code that narrates "Opening" must actually open
    # something.  This catches the "Download complete. Opening" -> no-op trap
    # even when the LLM's prose says nothing about opening.
    if result_low and re.search(r"""["'][^"']*opening[^"']*["']""", result_low):
        has_open = any(p in result_low for p in _OPEN_ACTION_PATTERNS)
        checks.append(ClaimCheck(
            claim="Code logs that it is opening the file/installer",
            sourceSection="generated_code",
            matchedByDiff=has_open,
            checkable=True,
            evidence="open action present" if has_open else None,
            mismatchReason=None if has_open else (
                "Code prints 'Opening' but performs no open action "
                "(no openFile/spawn/open/xdg-open/os.startfile/...)."
            ),
        ))

    passed = all(c.matchedByDiff for c in checks if c.checkable)
    return ClaimVerificationResult(passed=passed, checks=checks)


# ---------------------------------------------------------------------------
# Status derivation  (the no-fake-completion gate)
# ---------------------------------------------------------------------------

def derive_run_status(
    intent: RunIntent,
    files_modified_count: int,
    *,
    patch_applied: bool,
    claim_verification_passed: bool,
    runtime_error: bool = False,
    tests_failed: bool = False,
    no_change_intentional: bool = False,
) -> RunStatus:
    """Single source of truth for a run's terminal status.

    A modifying run can only become ``completed_with_patch`` when files truly
    changed, the patch was applied, and the claims match the diff.
    """
    if runtime_error:
        return RunStatus.FAILED_RUNTIME

    if intent in (RunIntent.READ_ONLY_REVIEW, RunIntent.REPO_SCAN, RunIntent.TEST_ONLY):
        return RunStatus.COMPLETED_READ_ONLY

    if intent == RunIntent.PROPOSED_PATCH:
        # A proposal is never applied to the repo.
        return RunStatus.COMPLETED_READ_ONLY

    # Modifying intents (apply_patch / autopilot_fix) from here on.
    if not claim_verification_passed:
        return RunStatus.FAILED_VERIFICATION

    if files_modified_count <= 0 or not patch_applied:
        if no_change_intentional and claim_verification_passed:
            return RunStatus.COMPLETED_NO_CHANGES
        return RunStatus.FAILED_PATCH_NOT_APPLIED

    if tests_failed:
        return RunStatus.FAILED_TESTS

    return RunStatus.COMPLETED_WITH_PATCH


# ---------------------------------------------------------------------------
# Health scoring  (must punish false completion)
# ---------------------------------------------------------------------------

def score_run_health(
    *,
    intent: RunIntent,
    patch_applied: bool,
    claim_verification_passed: bool,
    files_modified_count: int,
    target_file_changed: Optional[bool],
    validation: Validation,
    summary_matches_diff: bool,
    misleading_claims: bool,
) -> float:
    """0.0-1.0 run-quality score.  A fake completion lands near 0.05-0.20."""
    score = 0.0
    # Rewards
    if patch_applied:
        score += 0.25
    if claim_verification_passed:
        score += 0.20
    if validation.testsPassed or validation.buildPassed or validation.typecheckPassed:
        score += 0.20
    if target_file_changed:
        score += 0.15
    if summary_matches_diff:
        score += 0.10
    if not misleading_claims:
        score += 0.10

    # Hard penalties
    modifying = intent in MODIFYING_INTENTS
    if modifying and files_modified_count <= 0:
        score -= 0.40
    if not claim_verification_passed:
        score -= 0.35
    has_validation = any(c.passed for c in []) or any(
        v is not None for v in (
            validation.testsPassed, validation.buildPassed,
            validation.typecheckPassed, validation.lintPassed,
        )
    ) or validation.syntaxChecked
    if modifying and not has_validation:
        score -= 0.25
    if target_file_changed is False:
        score -= 0.20
    if modifying and not patch_applied:
        score -= 0.20

    return round(max(0.0, min(1.0, score)), 3)


# ---------------------------------------------------------------------------
# Headings guard (no "Updated Code" headings on a no-op run)
# ---------------------------------------------------------------------------

_APPLIED_HEADINGS = (
    "updated code", "what changed", "files modified", "patch applied",
    "i fixed it", "fixed it", "completed",
)


def sanitize_summary_for_no_change(summary: str) -> str:
    """If a run changed nothing, strip 'applied'-style headings and label it
    as a suggestion so the summary cannot imply a patch that does not exist.
    """
    if not summary:
        return "Suggestion only. No repository files were modified."
    low = summary.lower()
    if any(h in low for h in _APPLIED_HEADINGS):
        return (
            "Proposed code only — not applied to repo.\n\n"
            "Suggestion only. No repository files were modified.\n\n"
            + summary
        )
    return summary
