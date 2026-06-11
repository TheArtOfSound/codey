from __future__ import annotations

import math
from typing import Any

from codey.graph.engine import CodebaseGraph


class _FakeNodeView:
    def __init__(self, nodes: dict[str, dict[str, Any]]) -> None:
        self._nodes = nodes

    def __call__(self, data: bool = False) -> list[Any]:
        if data:
            return list(self._nodes.items())
        return list(self._nodes)

    def __iter__(self):
        return iter(self._nodes)

    def get(self, node_id: str, default: Any = None) -> dict[str, Any] | Any:
        return self._nodes.get(node_id, default)

    def __getitem__(self, node_id: str) -> dict[str, Any]:
        return self._nodes[node_id]


class _FakeDirectedGraph:
    def __init__(self) -> None:
        self.nodes = _FakeNodeView(
            {
                "file_a": {
                    "file_path": "a.py",
                    "_external_deps": [
                        {"target": "pkg", "kind": "import", "weight": "1e10000"},
                        {"target": "negative", "kind": "import", "weight": -5.0},
                        "malformed",
                    ],
                },
                "file_b": {"file_path": "b.py"},
            }
        )

    def out_edges(self, node_id: str, data: bool = False) -> list[tuple[Any, ...]]:
        if node_id == "file_a":
            edge = ("file_a", "file_b", {"weight": "bad"})
            return [edge] if data else [edge[:2]]
        return []

    def in_edges(self, _node_id: str, data: bool = False) -> list[tuple[Any, ...]]:
        return []


class _FakeComplexityGraph:
    def __init__(self) -> None:
        self.nodes = _FakeNodeView(
            {
                "file_a": {"file_path": "a.py", "complexity": "bad"},
                "func_a": {"file_path": "a.py", "complexity": "1e10000"},
                "func_b": {"file_path": "a.py", "complexity": -3.0},
            }
        )


class _FakeExternalDependencyGraph:
    def __init__(self) -> None:
        self.nodes = _FakeNodeView(
            {
                "file_a": {"file_path": "a.py", "_external_deps": None},
                "func_a": {"file_path": "a.py", "_external_deps": {"target": "pkg"}},
                "func_b": {"file_path": "a.py", "_external_deps": "bad"},
            }
        )

    def out_edges(self, _node_id: str, data: bool = False) -> list[tuple[Any, ...]]:
        return []

    def in_edges(self, _node_id: str, data: bool = False) -> list[tuple[Any, ...]]:
        return []


class _FakeImpactGraph:
    def __init__(self) -> None:
        self.nodes = _FakeNodeView(
            {
                "file_a": {"file_path": "a.py"},
                "file_b": {"file_path": "b.py"},
                "file_c": {"file_path": "c.py"},
                "file_d": {"file_path": "d.py"},
            }
        )

    def __contains__(self, node_id: str) -> bool:
        return node_id in self.nodes._nodes

    def out_edges(self, node_id: str, data: bool = False) -> list[tuple[Any, ...]]:
        if node_id == "file_a":
            edges = [
                ("file_a", "file_b", {"weight": "bad"}),
                ("file_a", "file_c", {"weight": "1e10000"}),
                ("file_a", "file_d", {"weight": None}),
            ]
            return edges if data else [edge[:2] for edge in edges]
        return []


def test_coupling_score_coerces_malformed_dependency_weights() -> None:
    graph = CodebaseGraph.__new__(CodebaseGraph)
    graph._graph = _FakeDirectedGraph()
    graph._cache_version = 0
    graph._cache = {}

    score = graph.coupling_score("a.py")

    assert math.isfinite(score)
    assert score == 1_000_003.0


def test_module_complexity_coerces_malformed_complexity_values() -> None:
    graph = CodebaseGraph.__new__(CodebaseGraph)
    graph._graph = _FakeComplexityGraph()
    graph._cache_version = 0
    graph._cache = {}

    score = graph.module_complexity("a.py")

    assert math.isfinite(score)
    assert score == 1_000_000.0 / 3.0


def test_external_dependency_metrics_normalize_malformed_containers() -> None:
    graph = CodebaseGraph.__new__(CodebaseGraph)
    graph._graph = _FakeExternalDependencyGraph()
    graph._cache_version = 0
    graph._cache = {}

    stats = graph.module_dependency_stats("a.py")

    assert stats["external_deps"] == 1
    assert stats["fanout"] == 1
    assert graph.coupling_score("a.py") == 1.0
    assert graph.cohesion_score("a.py") == 0.0


def test_impact_radius_coerces_malformed_weights_and_threshold() -> None:
    graph = CodebaseGraph.__new__(CodebaseGraph)
    graph._graph = _FakeImpactGraph()
    graph._cache_version = 0
    graph._cache = {}

    radius = graph.impact_radius("file_a", threshold="bad")

    assert radius == {"file_b", "file_c", "file_d"}


def test_high_stress_components_coerces_malformed_threshold() -> None:
    graph = CodebaseGraph.__new__(CodebaseGraph)
    graph._graph = _FakeImpactGraph()
    graph._cache_version = 0
    graph._cache = {}

    scores = {"file_a": 0.8, "file_b": 0.6, "file_c": 1.2, "file_d": 0.0}
    graph.stress_score = lambda node_id: scores[node_id]

    results = graph.get_high_stress_components(threshold="bad")

    assert results == [("file_c", 1.2), ("file_a", 0.8)]
