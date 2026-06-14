"""Acceptance tests for Codey patch receipts + claim verification.

These encode the non-negotiable guarantee: a modifying run cannot be reported
as successfully completed unless repo state, diff, claims, and receipt agree.
"""

from __future__ import annotations

from codey.saas.sessions.patch_receipt import (
    RunIntent,
    RunStatus,
    Validation,
    compute_diff,
    derive_run_status,
    extract_claims,
    score_run_health,
    verify_patch_claims,
)


# --- Test 1: apply-patch run with zero files changed --------------------------

def test_apply_patch_zero_files_is_not_plain_completed():
    status = derive_run_status(
        RunIntent.APPLY_PATCH, 0,
        patch_applied=False, claim_verification_passed=True,
    )
    assert status in (RunStatus.FAILED_PATCH_NOT_APPLIED, RunStatus.COMPLETED_NO_CHANGES)
    assert status is not RunStatus.COMPLETED_WITH_PATCH
    assert status.value != "completed"


def test_apply_patch_zero_files_intentional_is_no_changes():
    status = derive_run_status(
        RunIntent.APPLY_PATCH, 0,
        patch_applied=False, claim_verification_passed=True,
        no_change_intentional=True,
    )
    assert status is RunStatus.COMPLETED_NO_CHANGES


# --- Test 2: false claim ------------------------------------------------------

def test_false_claim_removed_console_import_fails():
    result = verify_patch_claims(
        ["Removed the console import."],
        diff_text="",            # no change to console import
        files_changed=[],
    )
    assert result.passed is False
    status = derive_run_status(
        RunIntent.APPLY_PATCH, 0,
        patch_applied=False, claim_verification_passed=result.passed,
    )
    assert status is RunStatus.FAILED_VERIFICATION


# --- Test 3: real claim -------------------------------------------------------

def test_real_claim_removed_console_import_passes():
    diff = '--- a/x.js\n+++ b/x.js\n- import { console } from "console";\n'
    result = verify_patch_claims(
        ["Removed the console import."],
        diff_text=diff,
        files_changed=["x.js"],
    )
    assert result.passed is True


# --- Test 4: code says "Opening" but no open action ---------------------------

def test_opening_log_without_open_action_fails():
    code = 'await download(url, target);\nconsole.log("Download complete. Opening");\n'
    result = verify_patch_claims(
        claims=[],
        diff_text="+ " + code.replace("\n", "\n+ "),
        files_changed=["qev-workspace.js"],
        result_content=code,
    )
    assert result.passed is False
    assert any("open" in (c.mismatchReason or "").lower() for c in result.checks)


def test_opening_log_with_open_action_passes():
    code = (
        'await download(url, target);\n'
        'console.log("Download complete. Opening installer.");\n'
        'await openFile(target);\n'
    )
    result = verify_patch_claims(
        claims=["Opened the downloaded installer."],
        diff_text="+ " + code.replace("\n", "\n+ "),
        files_changed=["qev-workspace.js"],
        result_content=code,
    )
    assert result.passed is True


# --- Test 5: proposed patch mode ----------------------------------------------

def test_proposed_patch_zero_files_is_read_only():
    status = derive_run_status(
        RunIntent.PROPOSED_PATCH, 0,
        patch_applied=False, claim_verification_passed=True,
    )
    assert status is RunStatus.COMPLETED_READ_ONLY


# --- Test 6: modifying run without validation ---------------------------------

def test_modifying_run_without_validation_completes_but_is_capped():
    validation = Validation(claimVerificationPassed=True, patchApplied=True, filesModifiedCount=1)
    status = derive_run_status(
        RunIntent.APPLY_PATCH, 1,
        patch_applied=True, claim_verification_passed=True,
    )
    assert status is RunStatus.COMPLETED_WITH_PATCH
    score = score_run_health(
        intent=RunIntent.APPLY_PATCH, patch_applied=True,
        claim_verification_passed=True, files_modified_count=1,
        target_file_changed=True, validation=validation,
        summary_matches_diff=True, misleading_claims=False,
    )
    # No tests/build/typecheck/lint/syntax ran -> -0.25 penalty caps the score.
    assert score < 0.80


# --- Test 7: target file not changed ------------------------------------------

def test_target_file_not_changed_fails_verification():
    result = verify_patch_claims(
        ["Updated packages/qev-cli/bin/qev-workspace.js to fix the installer."],
        diff_text="--- a/other.js\n+++ b/other.js\n+ // unrelated\n",
        files_changed=["other.js"],
    )
    assert result.passed is False
    status = derive_run_status(
        RunIntent.APPLY_PATCH, 1,
        patch_applied=True, claim_verification_passed=result.passed,
    )
    assert status is RunStatus.FAILED_VERIFICATION


# --- The exact original failure: qev-workspace.js -----------------------------

QEV_FALSE_SUMMARY = """
Updated packages/qev-cli/bin/qev-workspace.js.
Removed the else clause for cleaner control flow.
Removed the unused console import.
Fixed the structural issue so the installer opens the downloaded file.
"""

# Diff that does NONE of what the summary claims (the real bug).
QEV_NOOP_DIFF = (
    "--- a/packages/qev-cli/bin/qev-workspace.js\n"
    "+++ b/packages/qev-cli/bin/qev-workspace.js\n"
    "+ // touched a comment\n"
)
# The resulting file STILL contains everything the summary claims it removed.
QEV_NOOP_RESULT = (
    'import { console } from "console";\n'
    'async function install(asset, target) {\n'
    '  if (ok) {\n'
    '    await download(asset.browser_download_url, target);\n'
    '    console.log("Download complete. Opening");\n'
    '  } else {\n'
    '    fail();\n'
    '  }\n'
    '}\n'
)


def test_qev_original_failure_is_now_caught():
    claims = extract_claims(QEV_FALSE_SUMMARY)
    assert claims, "claims should be extracted from the summary"
    result = verify_patch_claims(
        claims, diff_text=QEV_NOOP_DIFF,
        files_changed=["packages/qev-cli/bin/qev-workspace.js"],
        result_content=QEV_NOOP_RESULT,
    )
    assert result.passed is False
    # At least the else, console-import, and opening claims must be flagged.
    flagged = " ".join((c.mismatchReason or "").lower() for c in result.mismatches)
    assert "else" in flagged
    assert "import" in flagged
    assert "open" in flagged

    status = derive_run_status(
        RunIntent.APPLY_PATCH, 0,
        patch_applied=False, claim_verification_passed=result.passed,
    )
    assert status is RunStatus.FAILED_VERIFICATION

    score = score_run_health(
        intent=RunIntent.APPLY_PATCH, patch_applied=False,
        claim_verification_passed=False, files_modified_count=0,
        target_file_changed=False, validation=Validation(),
        summary_matches_diff=False, misleading_claims=True,
    )
    assert score <= 0.20, f"fake completion must score low, got {score}"


def test_qev_real_fix_passes():
    original = {
        "packages/qev-cli/bin/qev-workspace.js": (
            'import { console } from "console";\n'
            'async function install(asset, target) {\n'
            '  if (ok) {\n'
            '    await download(asset.browser_download_url, target);\n'
            '    console.log("Download complete. Opening");\n'
            '  } else {\n'
            '    fail();\n'
            '  }\n'
            '}\n'
        )
    }
    fixed = {
        "packages/qev-cli/bin/qev-workspace.js": (
            'async function install(asset, target) {\n'
            '  await download(asset.browser_download_url, target);\n'
            '  console.log("Download complete. Opening installer.");\n'
            '  await openFile(target);\n'
            '}\n'
        )
    }
    diff_text, changes, diff_hash = compute_diff(original, fixed)
    assert diff_hash
    assert any(c.path.endswith("qev-workspace.js") for c in changes)
    result = verify_patch_claims(
        [
            "Removed the unused console import.",
            "Removed the else clause.",
            "Opened the downloaded installer instead of only logging 'Opening'.",
            "Updated packages/qev-cli/bin/qev-workspace.js.",
        ],
        diff_text=diff_text,
        files_changed=[c.path for c in changes],
        result_content=fixed["packages/qev-cli/bin/qev-workspace.js"],
    )
    assert result.passed is True, [c.mismatchReason for c in result.mismatches]
    status = derive_run_status(
        RunIntent.APPLY_PATCH, len(changes),
        patch_applied=True, claim_verification_passed=True,
    )
    assert status is RunStatus.COMPLETED_WITH_PATCH


# --- compute_diff honesty -----------------------------------------------------

def test_no_change_produces_empty_diff_and_zero_files():
    same = {"a.js": "x = 1\n"}
    diff_text, changes, diff_hash = compute_diff(same, same)
    assert diff_text == ""
    assert changes == []
    assert diff_hash == ""


def test_scoring_real_patch_beats_fake():
    fake = score_run_health(
        intent=RunIntent.APPLY_PATCH, patch_applied=False,
        claim_verification_passed=False, files_modified_count=0,
        target_file_changed=False, validation=Validation(),
        summary_matches_diff=False, misleading_claims=True,
    )
    real = score_run_health(
        intent=RunIntent.APPLY_PATCH, patch_applied=True,
        claim_verification_passed=True, files_modified_count=2,
        target_file_changed=True,
        validation=Validation(testsPassed=True, syntaxChecked=True),
        summary_matches_diff=True, misleading_claims=False,
    )
    assert fake <= 0.20
    assert real >= 0.85
    assert real - fake > 0.6


def test_claim_naming_unchanged_files_fails_even_with_coincidental_token():
    # The sentence has a removal verb and "GEMINI" happens to be in the removed
    # README text, but it names two code files that were NOT changed.
    result = verify_patch_claims(
        [
            "Updated app/api/generate/route.ts to fix the Gemini prompt handling and "
            "removed the unused Stripe webhook handler in app/api/stripe/webhook/route.ts."
        ],
        diff_text="--- a/README.md\n+++ b/README.md\n-Set the GEMINI_API_KEY here\n+New overview\n",
        files_changed=["README.md"],
    )
    assert result.passed is False
    assert any("not in the changed set" in (c.mismatchReason or "") for c in result.checks)


def test_claim_naming_changed_file_passes():
    result = verify_patch_claims(
        ["Updated README.md with an accurate overview."],
        diff_text="--- a/README.md\n+++ b/README.md\n+overview\n",
        files_changed=["README.md"],
    )
    assert result.passed is True
