"""LOLM / NFET run governor — the control plane for Codey coding runs.

Architecture split this implements:

    Writer model (Claude / Codex / GPT) = produces patches
    LOLM / NFET (this module)           = governs: measures repo-operation
                                          uncertainty, computes a control
                                          field, selects actions, and BLOCKS
                                          dishonest completions
    Codey                               = product shell + execution + receipts

The governor does not write code. It watches a run, turns the run's reality
(diff, claims-vs-diff, files read, NFET structural delta, validation) into a
repo-control field ``E_code``, decides what should happen next, and — critically
— refuses to let a modifying run report success unless the repo state, diff,
claims, and validation agree.

Pure functions + dataclasses (no I/O) so it is fully unit-testable and safe to
call from the run-completion path.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from codey.saas.sessions.patch_receipt import (
    MODIFYING_INTENTS,
    ClaimVerificationResult,
    FileChange,
    RunIntent,
    RunStatus,
    Validation,
)


# ---------------------------------------------------------------------------
# Signals + actions
# ---------------------------------------------------------------------------

@dataclass
class CodeyControlSignals:
    """Repo-operation uncertainty, not just language uncertainty. All 0..1."""
    promptAmbiguity: float = 0.0
    repoUnderstanding: float = 0.0
    targetFileConfidence: float = 0.0
    patchConfidence: float = 0.0
    diffRisk: float = 0.0
    testNeed: float = 0.0
    claimMismatchRisk: float = 0.0
    runtimeRisk: float = 0.0
    dependencyRisk: float = 0.0
    securityRisk: float = 0.0
    regressionRisk: float = 0.0
    completionHonestyRisk: float = 0.0
    costPressure: float = 0.0


class CodeyAgentAction(str, Enum):
    INSPECT_REPO = "inspect_repo"
    READ_TARGET_FILE = "read_target_file"
    TRACE_IMPORTS = "trace_imports"
    APPLY_PATCH = "apply_patch"
    RUN_LINT = "run_lint"
    RUN_TYPECHECK = "run_typecheck"
    RUN_TESTS = "run_tests"
    RUN_BUILD = "run_build"
    RUN_SMOKE_TEST = "run_smoke_test"
    COMPARE_CLAIMS_TO_DIFF = "compare_claims_to_diff"
    BRANCH_PATCH = "branch_patch"
    REJECT_PATCH = "reject_patch"
    MARK_COMPLETED_WITH_PATCH = "mark_completed_with_patch"
    MARK_COMPLETED_NO_CHANGES = "mark_completed_no_changes"
    MARK_FAILED_VERIFICATION = "mark_failed_verification"
    MARK_FAILED_PATCH_NOT_APPLIED = "mark_failed_patch_not_applied"
    IDLE = "idle"


# Weights for the E_code field. completionHonestyRisk + claimMismatchRisk
# dominate on purpose: dishonest completion is the cardinal sin.
DEFAULT_WEIGHTS: dict[str, float] = {
    "promptAmbiguity": 0.08,
    "repoUnderstandingGap": 0.12,   # applied to (1 - repoUnderstanding)
    "diffRisk": 0.12,
    "testNeed": 0.10,
    "claimMismatchRisk": 0.18,
    "runtimeRisk": 0.10,
    "regressionRisk": 0.10,
    "completionHonestyRisk": 0.20,
    "costPressure": 0.10,           # subtracted (more budget -> lower pressure to stop)
}

# Above this, a run cannot be silently completed — it must inspect/test/verify
# or fail honestly.
E_CODE_GATE = 0.55


@dataclass
class RunGovernorContext:
    intent: RunIntent
    prompt: str
    files_read: list[str]
    file_changes: list[FileChange]
    diff_text: str
    verification_passed: bool
    claims_total: int
    claims_mismatched: int
    validation: Validation
    patch_applied: bool
    repo_node_count: int = 0
    es_before: Optional[float] = None
    es_after: Optional[float] = None
    cost_pressure: Optional[float] = None  # 0..1 remaining-budget headroom


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


_PATH_RE = re.compile(r"[\w./-]+\.[A-Za-z0-9]{1,5}")


def compute_control_signals(ctx: RunGovernorContext) -> CodeyControlSignals:
    prompt = ctx.prompt or ""
    low = prompt.lower()
    files_modified = len(ctx.file_changes)
    diff_low = (ctx.diff_text or "").lower() + " " + " ".join(
        c.path.lower() for c in ctx.file_changes
    )

    # Prompt ambiguity: short / no concrete target → ambiguous.
    ambiguity = 0.8
    if len(prompt.split()) >= 8:
        ambiguity -= 0.3
    if _PATH_RE.search(prompt):
        ambiguity -= 0.25
    if any(v in low for v in ("fix", "remove", "add", "rename", "replace", "implement", "update")):
        ambiguity -= 0.2
    promptAmbiguity = _clamp01(ambiguity)

    # Repo understanding: how much relevant context was actually read.
    read = len(ctx.files_read)
    if ctx.repo_node_count > 0:
        repoUnderstanding = _clamp01(read / float(min(ctx.repo_node_count, 20) or 1))
    else:
        repoUnderstanding = _clamp01(0.4 + 0.12 * read)

    # Target-file confidence: were the files named in the brief actually located?
    targets = _PATH_RE.findall(prompt)
    touched = {f.lower() for f in ctx.files_read} | {c.path.lower() for c in ctx.file_changes}
    if targets:
        located = sum(
            1 for t in targets if any(t.lower() in f or f.endswith(t.lower()) for f in touched)
        )
        targetFileConfidence = _clamp01(located / len(targets))
    else:
        targetFileConfidence = 0.5

    # Diff magnitude + structural (NFET) delta.
    churn = sum(c.additions + c.deletions for c in ctx.file_changes)
    diffRisk = _clamp01(churn / 400.0)
    es_delta = 0.0
    if ctx.es_before is not None and ctx.es_after is not None:
        es_delta = ctx.es_after - ctx.es_before  # higher ES == more structural stress
        diffRisk = _clamp01(diffRisk + max(0.0, es_delta))
    regressionRisk = _clamp01(
        0.5 * diffRisk + max(0.0, es_delta) + (0.2 if files_modified > 3 else 0.0)
    )

    # Patch confidence.
    patchConfidence = 0.0
    if files_modified > 0:
        patchConfidence = (
            0.5
            + (0.3 if ctx.verification_passed else 0.0)
            + (0.2 if targetFileConfidence > 0.6 else 0.0)
        )
    patchConfidence = _clamp01(patchConfidence)

    # Claim-mismatch risk.
    if ctx.claims_total > 0:
        claimMismatchRisk = _clamp01(ctx.claims_mismatched / ctx.claims_total)
    else:
        claimMismatchRisk = 0.0 if ctx.verification_passed else 0.2

    # Completion-honesty risk — the cardinal gate signal.
    honesty = 0.0
    if not ctx.verification_passed:
        honesty = max(honesty, 0.9)
    if ctx.intent in MODIFYING_INTENTS and files_modified == 0:
        honesty = max(honesty, 0.85)
    if files_modified > 0 and not ctx.patch_applied:
        honesty = max(honesty, 0.7)
    completionHonestyRisk = _clamp01(honesty)

    # Validation pressure.
    ran_validation = any(
        v is not None
        for v in (
            ctx.validation.testsPassed,
            ctx.validation.buildPassed,
            ctx.validation.typecheckPassed,
            ctx.validation.lintPassed,
        )
    )
    testNeed = _clamp01(
        (0.6 if files_modified > 0 else 0.1) + 0.4 * diffRisk - (0.7 if ran_validation else 0.0)
    )
    runtimeRisk = _clamp01(
        (0.5 if files_modified > 0 else 0.0)
        + 0.3 * diffRisk
        - (0.4 if ctx.validation.syntaxChecked else 0.0)
        - (0.3 if ctx.validation.typecheckPassed else 0.0)
    )

    dependencyRisk = _clamp01(
        0.6 if any(k in diff_low for k in (
            "package.json", "requirements.txt", "pyproject", "import ", "require(",
            "go.mod", "cargo.toml", "package-lock",
        )) else 0.1
    )
    securityRisk = _clamp01(
        0.7 if any(k in diff_low for k in (
            "password", "secret", "token", "api_key", "apikey", "exec(", "subprocess",
            "eval(", "os.system", "child_process", "credential", "private_key",
        )) else 0.05
    )

    costPressure = _clamp01(ctx.cost_pressure if ctx.cost_pressure is not None else 0.3)

    return CodeyControlSignals(
        promptAmbiguity=round(promptAmbiguity, 3),
        repoUnderstanding=round(repoUnderstanding, 3),
        targetFileConfidence=round(targetFileConfidence, 3),
        patchConfidence=round(patchConfidence, 3),
        diffRisk=round(diffRisk, 3),
        testNeed=round(testNeed, 3),
        claimMismatchRisk=round(claimMismatchRisk, 3),
        runtimeRisk=round(runtimeRisk, 3),
        dependencyRisk=round(dependencyRisk, 3),
        securityRisk=round(securityRisk, 3),
        regressionRisk=round(regressionRisk, 3),
        completionHonestyRisk=round(completionHonestyRisk, 3),
        costPressure=round(costPressure, 3),
    )


def nfet_field_energy(
    signals: CodeyControlSignals,
    weights: Optional[dict[str, float]] = None,
) -> float:
    """E_code — the repo-control field. High means 'do not just complete'."""
    w = weights or DEFAULT_WEIGHTS
    e = (
        w["promptAmbiguity"] * signals.promptAmbiguity
        + w["repoUnderstandingGap"] * (1.0 - signals.repoUnderstanding)
        + w["diffRisk"] * signals.diffRisk
        + w["testNeed"] * signals.testNeed
        + w["claimMismatchRisk"] * signals.claimMismatchRisk
        + w["runtimeRisk"] * signals.runtimeRisk
        + w["regressionRisk"] * signals.regressionRisk
        + w["completionHonestyRisk"] * signals.completionHonestyRisk
        - w["costPressure"] * signals.costPressure
    )
    return round(max(0.0, min(1.0, e)), 3)


def select_actions(
    signals: CodeyControlSignals,
    e_code: float,
    *,
    intent: RunIntent,
    files_modified: int,
    patch_applied: bool,
) -> list[CodeyAgentAction]:
    """What the governor would have the writer/executor do next."""
    actions: list[CodeyAgentAction] = []
    if signals.repoUnderstanding < 0.5:
        actions += [CodeyAgentAction.INSPECT_REPO, CodeyAgentAction.READ_TARGET_FILE]
    if signals.dependencyRisk > 0.5:
        actions.append(CodeyAgentAction.TRACE_IMPORTS)

    actions.append(CodeyAgentAction.COMPARE_CLAIMS_TO_DIFF)

    if signals.testNeed > 0.5:
        actions += [CodeyAgentAction.RUN_TYPECHECK, CodeyAgentAction.RUN_TESTS]
        if signals.diffRisk > 0.5 or signals.regressionRisk > 0.5:
            actions.append(CodeyAgentAction.RUN_BUILD)

    # Terminal decision.
    if signals.claimMismatchRisk >= 0.5 or signals.completionHonestyRisk >= 0.6:
        if not (intent in MODIFYING_INTENTS and files_modified == 0):
            actions.append(CodeyAgentAction.REJECT_PATCH)
        if intent in MODIFYING_INTENTS and files_modified == 0 and signals.completionHonestyRisk >= 0.6:
            actions.append(CodeyAgentAction.MARK_FAILED_PATCH_NOT_APPLIED)
        else:
            actions.append(CodeyAgentAction.MARK_FAILED_VERIFICATION)
    elif files_modified == 0:
        actions.append(CodeyAgentAction.MARK_COMPLETED_NO_CHANGES)
    elif signals.patchConfidence < 0.4 and signals.diffRisk > 0.6:
        actions.append(CodeyAgentAction.BRANCH_PATCH)
    elif patch_applied:
        actions.append(CodeyAgentAction.MARK_COMPLETED_WITH_PATCH)
    else:
        actions.append(CodeyAgentAction.MARK_FAILED_PATCH_NOT_APPLIED)

    # De-dup, keep order.
    seen: set[str] = set()
    return [a for a in actions if not (a.value in seen or seen.add(a.value))]


def govern_completion(
    base_status: RunStatus,
    signals: CodeyControlSignals,
    e_code: float,
) -> tuple[RunStatus, str]:
    """The gate. Returns ``(final_status, reason)``.

    Even if a naive path would mark success, a high honesty/claim-mismatch
    signal forces an honest failure. The governor never *upgrades* a failure.
    """
    if base_status in (
        RunStatus.COMPLETED_WITH_PATCH,
        RunStatus.COMPLETED_NO_CHANGES,
    ):
        if signals.completionHonestyRisk >= 0.6 or signals.claimMismatchRisk >= 0.5:
            return (
                RunStatus.FAILED_VERIFICATION,
                f"Governor blocked completion: E_code={e_code}, "
                f"completionHonestyRisk={signals.completionHonestyRisk}, "
                f"claimMismatchRisk={signals.claimMismatchRisk}.",
            )
    reason = (
        f"E_code={e_code}; honesty={signals.completionHonestyRisk}; "
        f"claimMismatch={signals.claimMismatchRisk}; testNeed={signals.testNeed}; "
        f"diffRisk={signals.diffRisk}."
    )
    if e_code >= E_CODE_GATE and base_status in SUCCESS_AND_PATCH:
        reason = "High control energy — recommend validation before trusting this run. " + reason
    return base_status, reason


SUCCESS_AND_PATCH = frozenset({RunStatus.COMPLETED_WITH_PATCH})


# ---------------------------------------------------------------------------
# Merged LOLM + patch receipt
# ---------------------------------------------------------------------------

@dataclass
class CodeyLOLMRunReceipt:
    runId: str
    writerModel: str
    controllerModel: str
    repo: dict
    control: dict          # {signals, nfetFieldEnergy, selectedActions, reason}
    patch: dict            # {filesRead, filesChanged, diffHash, patchApplied}
    verification: dict     # {claimsMatchDiff, lint/typecheck/tests/build/smoke}
    finalStatus: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def build_lolm_receipt(
    *,
    run_id: str,
    writer_model: str,
    repo: dict,
    signals: CodeyControlSignals,
    e_code: float,
    actions: list[CodeyAgentAction],
    reason: str,
    files_read: list[str],
    file_changes: list[FileChange],
    diff_hash: str,
    patch_applied: bool,
    verification: ClaimVerificationResult,
    validation: Validation,
    final_status: RunStatus,
) -> CodeyLOLMRunReceipt:
    return CodeyLOLMRunReceipt(
        runId=run_id,
        writerModel=writer_model,
        controllerModel="LOLM/NFET",
        repo=repo,
        control={
            "signals": dataclasses.asdict(signals),
            "nfetFieldEnergy": e_code,
            "selectedActions": [a.value for a in actions],
            "reason": reason,
        },
        patch={
            "filesRead": list(files_read),
            "filesChanged": [c.path for c in file_changes],
            "diffHash": diff_hash,
            "patchApplied": patch_applied,
        },
        verification={
            "claimsMatchDiff": verification.passed,
            "lintPassed": validation.lintPassed,
            "typecheckPassed": validation.typecheckPassed,
            "testsPassed": validation.testsPassed,
            "buildPassed": validation.buildPassed,
            "smokeTestPassed": validation.smokeTestPassed,
        },
        finalStatus=final_status.value,
    )
