"""Tests for the LOLM/NFET run governor (control plane)."""

from __future__ import annotations

from codey.saas.sessions.patch_receipt import (
    ClaimVerificationResult,
    FileChange,
    RunIntent,
    RunStatus,
    Validation,
)
from codey.saas.sessions.run_governor import (
    CodeyAgentAction,
    RunGovernorContext,
    build_lolm_receipt,
    compute_control_signals,
    govern_completion,
    nfet_field_energy,
    select_actions,
)


def _fake_run_ctx() -> RunGovernorContext:
    # qev-style: claims false, nothing changed.
    return RunGovernorContext(
        intent=RunIntent.APPLY_PATCH,
        prompt="fix packages/qev-cli/bin/qev-workspace.js",
        files_read=[],
        file_changes=[],
        diff_text="",
        verification_passed=False,
        claims_total=4,
        claims_mismatched=3,
        validation=Validation(),
        patch_applied=False,
    )


def _clean_run_ctx() -> RunGovernorContext:
    fc = [FileChange(path="packages/qev-cli/bin/qev-workspace.js", additions=6, deletions=4, changeKind="modified")]
    return RunGovernorContext(
        intent=RunIntent.APPLY_PATCH,
        prompt="fix packages/qev-cli/bin/qev-workspace.js installer open bug",
        files_read=["packages/qev-cli/bin/qev-workspace.js"],
        file_changes=fc,
        diff_text="--- a/x\n+++ b/x\n+await openFile(target)\n-  } else {\n",
        verification_passed=True,
        claims_total=3,
        claims_mismatched=0,
        validation=Validation(syntaxChecked=True, testsPassed=True, typecheckPassed=True),
        patch_applied=True,
        repo_node_count=10,
        es_before=0.4,
        es_after=0.38,
    )


def test_fake_run_signals_are_dangerous():
    s = compute_control_signals(_fake_run_ctx())
    assert s.completionHonestyRisk >= 0.8
    assert s.claimMismatchRisk >= 0.5
    assert s.patchConfidence == 0.0


def test_clean_run_signals_are_calm():
    s = compute_control_signals(_clean_run_ctx())
    assert s.completionHonestyRisk == 0.0
    assert s.claimMismatchRisk == 0.0
    assert s.patchConfidence >= 0.7
    assert s.testNeed < 0.5  # tests were run


def test_e_code_higher_for_fake_than_clean():
    fake = nfet_field_energy(compute_control_signals(_fake_run_ctx()))
    clean = nfet_field_energy(compute_control_signals(_clean_run_ctx()))
    assert fake > clean
    assert fake >= 0.35
    assert clean < 0.3


def test_governor_blocks_fake_completion_even_if_base_says_completed():
    s = compute_control_signals(_fake_run_ctx())
    e = nfet_field_energy(s)
    # Pretend a naive path tried to mark it completed_with_patch.
    final, reason = govern_completion(RunStatus.COMPLETED_WITH_PATCH, s, e)
    assert final is RunStatus.FAILED_VERIFICATION
    assert "blocked" in reason.lower()


def test_governor_passes_clean_completion():
    s = compute_control_signals(_clean_run_ctx())
    e = nfet_field_energy(s)
    final, reason = govern_completion(RunStatus.COMPLETED_WITH_PATCH, s, e)
    assert final is RunStatus.COMPLETED_WITH_PATCH


def test_actions_fail_on_mismatch_and_test_on_need():
    s = compute_control_signals(_fake_run_ctx())
    e = nfet_field_energy(s)
    acts = select_actions(s, e, intent=RunIntent.APPLY_PATCH, files_modified=0, patch_applied=False)
    assert CodeyAgentAction.MARK_FAILED_PATCH_NOT_APPLIED in acts or CodeyAgentAction.MARK_FAILED_VERIFICATION in acts
    assert CodeyAgentAction.MARK_COMPLETED_WITH_PATCH not in acts

    # A risky-but-honest patch with no validation should be told to run tests.
    ctx = _clean_run_ctx()
    ctx.validation = Validation()  # nothing run
    ctx.file_changes = [FileChange(path="a.js", additions=300, deletions=120, changeKind="modified")]
    s2 = compute_control_signals(ctx)
    acts2 = select_actions(s2, nfet_field_energy(s2), intent=RunIntent.APPLY_PATCH,
                           files_modified=1, patch_applied=True)
    assert CodeyAgentAction.RUN_TESTS in acts2


def test_merged_receipt_shape():
    ctx = _clean_run_ctx()
    s = compute_control_signals(ctx)
    e = nfet_field_energy(s)
    acts = select_actions(s, e, intent=ctx.intent, files_modified=1, patch_applied=True)
    final, reason = govern_completion(RunStatus.COMPLETED_WITH_PATCH, s, e)
    receipt = build_lolm_receipt(
        run_id="r1", writer_model="claude-opus-4-8", repo={"name": "qev-cli"},
        signals=s, e_code=e, actions=acts, reason=reason,
        files_read=ctx.files_read, file_changes=ctx.file_changes,
        diff_hash="abc123", patch_applied=True,
        verification=ClaimVerificationResult(passed=True, checks=[]),
        validation=ctx.validation, final_status=final,
    )
    d = receipt.to_dict()
    assert d["controllerModel"] == "LOLM/NFET"
    assert d["writerModel"] == "claude-opus-4-8"
    assert d["control"]["nfetFieldEnergy"] == e
    assert d["finalStatus"] == "completed_with_patch"
    assert d["verification"]["claimsMatchDiff"] is True
