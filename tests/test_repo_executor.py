"""End-to-end tests for the repo executor — REAL git + REAL node --check.

These exercise the not-fakeable path: a patch is applied to a real working
tree, the diff/commits come from git, claims are checked against that real
diff, and `node --check` actually parses the result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest

from codey.saas.sessions.patch_receipt import RunStatus
from codey.saas.sessions.repo_executor import (
    RepoExecutor,
    apply_and_verify,
    branch_and_pick,
)

QEV_PATH = "packages/qev-cli/bin/qev-workspace.js"
BUGGY = (
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
FIXED = (
    'async function install(asset, target) {\n'
    '  await download(asset.browser_download_url, target);\n'
    '  console.log("Download complete. Opening installer.");\n'
    '  await openFile(target);\n'
    '}\n'
)
BROKEN = 'async function install( {\n  return;\n'  # invalid JS

_HAVE_GIT = shutil.which("git") is not None
_HAVE_NODE = shutil.which("node") is not None
pytestmark = pytest.mark.skipif(not (_HAVE_GIT and _HAVE_NODE),
                                reason="needs git + node for real validation")


def _baseline(tmp: str) -> RepoExecutor:
    ex = RepoExecutor.init_local(tmp)
    ex.write_files({QEV_PATH: BUGGY})
    ex.commit("baseline")
    return ex


def test_real_fix_commits_and_passes():
    tmp = tempfile.mkdtemp(prefix="codey-exec-fix-")
    try:
        ex = _baseline(tmp)
        before = ex.head()
        run = apply_and_verify(
            ex, files={QEV_PATH: FIXED},
            explanation=("Removed the unused console import. Removed the else clause. "
                         "Opened the downloaded installer instead of only logging Opening. "
                         f"Updated {QEV_PATH}."),
            repo={"name": "qev-cli", "runId": "r1"},
        )
        assert run.status is RunStatus.COMPLETED_WITH_PATCH
        assert run.validation.syntaxChecked is True          # node --check passed
        assert run.validation.claimVerificationPassed is True
        assert any(c.path.endswith("qev-workspace.js") for c in run.file_changes)
        assert run.receipt["diffHash"]
        assert run.receipt["commitBefore"] == before
        assert run.receipt["commitAfter"] and run.receipt["commitAfter"] != before
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_false_claims_against_real_diff_block_and_do_not_commit():
    tmp = tempfile.mkdtemp(prefix="codey-exec-false-")
    try:
        ex = _baseline(tmp)
        before = ex.head()
        # Patch only adds a comment; console import + else are still there.
        run = apply_and_verify(
            ex, files={QEV_PATH: "// touched\n" + BUGGY},
            explanation="Removed the console import. Removed the else clause.",
            repo={"name": "qev-cli"},
        )
        assert run.status is RunStatus.FAILED_VERIFICATION
        assert run.validation.claimVerificationPassed is False
        assert run.receipt["commitAfter"] in (None, before)   # not committed
        assert ex.head() == before
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_broken_syntax_blocks_completion():
    tmp = tempfile.mkdtemp(prefix="codey-exec-broken-")
    try:
        ex = _baseline(tmp)
        before = ex.head()
        run = apply_and_verify(
            ex, files={QEV_PATH: BROKEN},
            explanation=f"Updated {QEV_PATH} to simplify install.",
            repo={"name": "qev-cli"},
        )
        assert run.status is RunStatus.FAILED_TESTS       # node --check failed
        assert run.validation.syntaxChecked is False
        assert ex.head() == before                        # not committed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_branch_and_pick_chooses_the_valid_candidate():
    tmp = tempfile.mkdtemp(prefix="codey-exec-branch-")
    try:
        _baseline(tmp)
        candidates = [{"id": "broken", "code": BROKEN}, {"id": "good", "code": FIXED}]
        winner, summaries = branch_and_pick(
            tmp, candidates,
            explanation_for=lambda c: ("Removed the unused console import. Removed the else "
                                       f"clause. Opened the installer. Updated {QEV_PATH}."),
            files_for=lambda c: {QEV_PATH: c["code"]},
        )
        assert winner is not None
        assert winner.status is RunStatus.COMPLETED_WITH_PATCH
        assert len(summaries) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


PKG = '{"name":"t","version":"1.0.0","scripts":{"test":"node test.js"}}\n'
TEST_JS = (
    'const { add } = require("./index.js");\n'
    'if (add(2, 3) !== 5) { console.error("FAIL"); process.exit(1); }\n'
    'console.log("ok");\n'
)


def _npm_baseline(tmp: str, add_body: str) -> RepoExecutor:
    ex = RepoExecutor.init_local(tmp)
    ex.write_files({
        "package.json": PKG,
        "test.js": TEST_JS,
        "index.js": f"module.exports.add = (a, b) => {add_body};\n",
    })
    ex.commit("baseline")
    return ex


def test_real_npm_test_runs_and_passes():
    tmp = tempfile.mkdtemp(prefix="codey-npm-pass-")
    try:
        ex = _npm_baseline(tmp, "a + b")
        run = apply_and_verify(
            ex, files={"index.js": "module.exports.add = (a, b) => a + b; // tidy\n"},
            explanation="Updated index.js with a clarifying comment.", repo={"name": "t"},
        )
        assert run.validation.testsPassed is True          # REAL npm test ran + passed
        assert run.status is RunStatus.COMPLETED_WITH_PATCH
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_real_npm_test_failure_blocks_completion():
    tmp = tempfile.mkdtemp(prefix="codey-npm-fail-")
    try:
        ex = _npm_baseline(tmp, "a + b")
        before = ex.head()
        # Patch BREAKS add() -> the real npm test fails.
        run = apply_and_verify(
            ex, files={"index.js": "module.exports.add = (a, b) => a - b;\n"},
            explanation="Updated index.js.", repo={"name": "t"},
        )
        assert run.validation.testsPassed is False         # REAL npm test ran + failed
        assert run.status is RunStatus.FAILED_TESTS
        assert ex.head() == before                         # not committed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
