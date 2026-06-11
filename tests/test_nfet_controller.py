from __future__ import annotations

from pathlib import Path

from codey.graph.engine import CodebaseGraph
from codey.nfet.controller import NFETController
from codey.parser.extractor import CodeEdge, CodeNode, extract_from_source, parse_directory
from codey.saas.intelligence.router import ExecutionMode, TaskRouter


def _build_test_graph() -> CodebaseGraph:
    graph = CodebaseGraph()
    nodes = [
        CodeNode(
            id="file_a",
            kind="file",
            name="a.py",
            file_path="a.py",
            line_start=1,
            line_end=50,
        ),
        CodeNode(
            id="func_a",
            kind="function",
            name="auth_handler",
            file_path="a.py",
            line_start=2,
            line_end=28,
            complexity=9.0,
        ),
        CodeNode(
            id="file_b",
            kind="file",
            name="b.py",
            file_path="b.py",
            line_start=1,
            line_end=40,
        ),
        CodeNode(
            id="func_b",
            kind="function",
            name="shared_util",
            file_path="b.py",
            line_start=2,
            line_end=18,
            complexity=2.0,
        ),
    ]
    edges = [
        CodeEdge(source="file_a", target="func_a", kind="data_flow", weight=1.0),
        CodeEdge(source="file_b", target="func_b", kind="data_flow", weight=1.0),
        CodeEdge(source="func_b", target="file_b", kind="data_flow", weight=1.0),
        CodeEdge(source="file_a", target="file_b", kind="import", weight=2.0),
        CodeEdge(source="func_a", target="func_b", kind="call", weight=2.0),
        CodeEdge(source="func_a", target="file_b", kind="state_dep", weight=1.0),
    ]
    graph.build_from_nodes_edges(nodes, edges)
    return graph


def test_extract_from_source_compat_wrapper() -> None:
    nodes, edges = extract_from_source(
        "def add(a, b):\n    return a + b\n",
        filename="adder.py",
        language="python",
    )
    assert any(node.kind == "file" for node in nodes)
    assert any(node.file_path == "adder.py" for node in nodes)
    assert isinstance(edges, list)


def test_controller_ranks_hotspots_and_candidates() -> None:
    graph = _build_test_graph()
    controller = NFETController()

    repo_state = controller.analyze(
        graph,
        goal="stabilize auth flow in a.py",
        target_file="a.py",
    )

    assert repo_state.hotspots
    assert repo_state.hotspots[0].file_path == "a.py"

    candidates = controller.rank_interventions(
        graph,
        goal="stabilize auth flow in a.py",
        target_file="a.py",
        repo_state=repo_state,
        limit=3,
    )
    assert candidates
    assert candidates[0].target_file_path == "a.py"
    assert candidates[0].predicted_repo_es_delta > 0

    simulation = controller.simulate_action(
        graph,
        candidates[0],
        goal="stabilize auth flow in a.py",
        target_file="a.py",
        repo_state=repo_state,
    )
    assert simulation.after_repo_es > simulation.before_repo_es


def test_router_escalates_when_nfet_is_critical() -> None:
    router = TaskRouter()
    config = router.classify(
        "implement a new auth endpoint",
        {
            "nfet_phase": "critical",
            "nfet_hotspots": 4,
            "nfet_focus_risk": 0.8,
        },
    )
    assert config.mode == ExecutionMode.REASON_THEN_IMPLEMENT


def test_router_keeps_prompt_workspace_on_interactive_code_path() -> None:
    router = TaskRouter()
    config = router.classify(
        "implement the highest-value hardening change for this repository",
        {
            "surface": "prompt_workspace",
            "task_hint": "code_generation",
            "nfet_phase": "critical",
            "nfet_hotspots": 4,
            "nfet_focus_risk": 0.8,
        },
    )
    assert config.mode == ExecutionMode.SINGLE
    assert config.primary in {"code_generation", "default"}


def test_router_tolerates_malformed_nfet_context_values() -> None:
    router = TaskRouter()
    config = router.classify(
        "implement a new auth endpoint",
        {
            "nfet_phase": "caution",
            "nfet_hotspots": {"bad": "shape"},
            "nfet_focus_risk": ["bad"],
            "nfet_goal_pressure": object(),
            "codebase_tokens": {"also": "bad"},
        },
    )

    assert config.mode == ExecutionMode.SINGLE
    assert config.metadata["codebase_tokens"] == 0
    assert config.metadata["nfet_hotspots"] == 0
    assert config.metadata["nfet_focus_risk"] == 0.0
    assert config.metadata["nfet_goal_pressure"] == 0.0


def test_controller_returns_neutral_state_when_no_supported_files_exist() -> None:
    graph = CodebaseGraph()
    controller = NFETController()

    repo_state = controller.analyze(graph, goal="stabilize the repo")

    assert repo_state.phase == "unknown"
    assert repo_state.global_es == 0.5
    assert repo_state.hotspots == []


def test_controller_clamp_rejects_non_finite_values() -> None:
    assert NFETController._clamp(float("nan")) == 0.0
    assert NFETController._clamp(float("inf")) == 0.0
    assert NFETController._clamp(float("-inf")) == 0.0
    assert NFETController._clamp(10**10000) == 0.0
    assert NFETController._clamp(1.5) == 1.0


def test_controller_normalize_stress_handles_malformed_values() -> None:
    assert NFETController._normalize_stress(None) > 0.999
    assert NFETController._normalize_stress("bad") > 0.999
    assert NFETController._normalize_stress(float("inf")) > 0.999
    assert NFETController._normalize_stress("-1") == 0.0


def test_parse_directory_uses_root_relative_paths(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    file_path = source / "app.py"
    file_path.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    nodes, _edges = parse_directory(tmp_path)

    assert any(node.file_path == "src/app.py" for node in nodes)
