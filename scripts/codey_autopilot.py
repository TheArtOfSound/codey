#!/usr/bin/env python3
"""Codey autopilot — the end-to-end repo loop.

clone (commitBefore) -> apply writer patch -> REAL git diff -> verify claims
-> run validation -> commit (commitAfter) -> governor gate -> push -> open PR
with the patch receipt. The PR is only opened when the run reaches a verified
completed_with_patch status; otherwise it aborts and prints why.

Usage:
  codey_autopilot.py --repo owner/name --branch codey/fix-x --patch spec.json
                     [--base main] [--project-commands] [--no-pr]

spec.json: {"files": {"path": "content"}, "explanation": "...",
            "title": "PR title", "body": "PR body"}
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

from codey.saas.sessions.patch_receipt import RunStatus
from codey.saas.sessions.repo_executor import RepoExecutor, apply_and_verify


def sh(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--patch", required=True)
    ap.add_argument("--base", default="main")
    ap.add_argument("--project-commands", action="store_true")
    ap.add_argument("--no-pr", action="store_true")
    ap.add_argument("--writer", default="claude-opus-4-8")
    a = ap.parse_args()

    spec = json.load(open(a.patch))
    token = (sh(["gh", "auth", "token"]).stdout or "").strip() or None
    dest = tempfile.mkdtemp(prefix="codey-autopilot-")
    repo_dir = os.path.join(dest, "repo")

    print(f"[1] clone {a.repo} (branch {a.base})")
    ex = RepoExecutor.clone(f"https://github.com/{a.repo}.git", repo_dir,
                            branch=a.base, token=token, timeout=180)
    sh(["git", "checkout", "-b", a.branch], cwd=ex.workdir)

    print("[2-6] apply -> diff -> verify claims -> validate -> govern")
    run = apply_and_verify(
        ex, files=spec["files"], explanation=spec["explanation"],
        writer_model=a.writer, repo={"name": a.repo, "branch": a.branch, "runId": a.branch},
        allow_project_commands=a.project_commands,
        commit_message=spec.get("title", "Codey: verified patch"),
    )
    r = run.receipt
    print(f"    status={run.status.value}  files={len(run.file_changes)}  "
          f"E_code={run.e_code}  claimsOK={r['validation']['claimVerificationPassed']}  "
          f"commitAfter={r.get('commitAfter')}")
    for c in r.get("claimsMade", []):
        if c.get("checkable", True) and not c.get("matchedByDiff"):
            print("    MISMATCH:", c.get("mismatchReason"))

    if run.status is not RunStatus.COMPLETED_WITH_PATCH:
        print(f"[x] not pushing — governor did not certify the run ({run.status.value}).")
        return 2

    print(f"[7] push {a.branch}")
    push_url = f"https://x-access-token:{token}@github.com/{a.repo}.git" if token else "origin"
    p = sh(["git", "push", push_url, f"HEAD:refs/heads/{a.branch}"], cwd=ex.workdir)
    if p.returncode != 0:
        print("    push failed:", p.stderr[-400:]); return 3

    if a.no_pr:
        print("[done] pushed; --no-pr set."); return 0

    body = (spec.get("body", "") + "\n\n---\n### Codey patch receipt (proof)\n"
            f"- final status: `{r['status']}`\n"
            f"- claims verified against diff: `{r['validation']['claimVerificationPassed']}`\n"
            f"- patch applied: `{r['validation']['patchApplied']}`  ·  files changed: "
            + ", ".join(f"`{c['path']}`" for c in r["filesChanged"]) + "\n"
            f"- diffHash: `{r['diffHash'][:16]}`\n"
            f"- commitBefore → commitAfter: `{r.get('commitBefore','')[:10]}` → `{(r.get('commitAfter') or '')[:10]}`\n"
            f"- LOLM/NFET E_code: `{r['control']['nfetFieldEnergy']}`  ·  governor: {r['control']['reason']}\n\n"
            "🤖 Writer model produced the patch; **LOLM/NFET governed it** and Codey verified "
            "the claims against the real diff before this PR was opened.")
    bf = os.path.join(dest, "body.md")
    open(bf, "w").write(body)
    print(f"[8] open PR -> {a.base}")
    pr = sh(["gh", "pr", "create", "-R", a.repo, "--base", a.base, "--head", a.branch,
             "--title", spec["title"], "--body-file", bf])
    print(pr.stdout.strip() or pr.stderr.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
