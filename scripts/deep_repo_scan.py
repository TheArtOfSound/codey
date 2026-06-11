#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if TYPE_CHECKING:
    from codey.graph import CodebaseGraph

_URL_CREDENTIAL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&#](?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|token|secret|password)=)[^&\s]+"
)
_GIT_TIMEOUT_SECONDS = 5.0


def _run_git(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    value = result.stdout.strip()
    return value or None


def _git_metadata(root: Path) -> dict[str, object]:
    dirty_output = _run_git(["status", "--porcelain"], root) or ""
    return {
        "branch": _run_git(["rev-parse", "--abbrev-ref", "HEAD"], root),
        "commit": _run_git(["rev-parse", "HEAD"], root),
        "origin": _redact_remote_url(_run_git(["remote", "get-url", "origin"], root)),
        "dirty_files": len([line for line in dirty_output.splitlines() if line.strip()]),
    }


def _redact_remote_url(value: str | None) -> str | None:
    if value is None:
        return None
    value = _URL_CREDENTIAL_RE.sub(r"\1***@", value)
    return _URL_QUERY_SECRET_RE.sub(r"\1***", value)


def _build_graph(root: Path) -> CodebaseGraph:
    from codey.graph import CodebaseGraph
    from codey.parser import parse_directory

    nodes, edges = parse_directory(root)
    graph = CodebaseGraph()
    graph.build_from_nodes_edges(nodes, edges)
    return graph


def build_report(root: Path, goal: str) -> dict[str, object]:
    from codey.nfet import NFETSweep
    from codey.nfet.controller import NFETController

    graph = _build_graph(root)

    sweep = NFETSweep()
    sweep.calibrate(graph)
    controller = NFETController(sweep_engine=sweep)
    repo_state = controller.analyze(graph, goal=goal, top_k=10)
    candidates = controller.rank_interventions(
        graph,
        goal=goal,
        repo_state=repo_state,
        limit=10,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "goal": goal,
        "git": _git_metadata(root),
        "summary": {
            "phase": repo_state.phase,
            "global_es": repo_state.global_es,
            "global_kappa": repo_state.global_kappa,
            "global_sigma": repo_state.global_sigma,
            "total_nodes": repo_state.total_nodes,
            "total_edges": repo_state.total_edges,
            "highest_stress_component": repo_state.highest_stress_component,
            "highest_stress_value": repo_state.highest_stress_value,
            "hotspot_count": len(repo_state.hotspots),
            "candidate_count": len(candidates),
        },
        "hotspots": [hotspot.to_dict() for hotspot in repo_state.hotspots],
        "candidates": [candidate.to_dict() for candidate in candidates],
        "guidance": controller.build_guidance(repo_state, candidates, limit=5),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a deep NFET repo scan and emit a JSON report.",
    )
    parser.add_argument("root", nargs="?", default=".", help="Repository root to scan")
    parser.add_argument(
        "--goal",
        default="continuous gap fixing, optimization, hardening, and upgrade planning",
        help="Planning goal fed into NFET intervention ranking",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(json.dumps({"error": f"{root} is not a directory"}), file=sys.stderr)
        return 1

    report = build_report(root, args.goal)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
