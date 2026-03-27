"""Codebase Graph Engine — maintains a real-time NetworkX directed graph of the codebase."""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

import networkx as nx

from codey.parser.extractor import CodeEdge, CodeNode

logger = logging.getLogger(__name__)


class CodebaseGraph:
    """A live directed graph built from parsed CodeNode/CodeEdge structures.

    Supports full rebuilds and incremental per-file updates with automatic
    cache invalidation for expensive graph metric computations.
    """

    def __init__(self) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._cache_version: int = 0
        self._cache: dict[str, tuple[int, Any]] = {}

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _invalidate_cache(self) -> None:
        """Bump the cache version so all cached results become stale."""
        self._cache_version += 1

    def _get_cached(self, key: str) -> Any | None:
        """Return cached value if still valid, else None."""
        entry = self._cache.get(key)
        if entry is not None and entry[0] == self._cache_version:
            return entry[1]
        return None

    def _set_cached(self, key: str, value: Any) -> Any:
        """Store a value in cache at the current version and return it."""
        self._cache[key] = (self._cache_version, value)
        return value

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def build_from_nodes_edges(
        self, nodes: list[CodeNode], edges: list[CodeEdge]
    ) -> None:
        """Build the full graph from parser output, replacing any existing graph."""
        self._graph.clear()
        self._invalidate_cache()

        for node in nodes:
            self._graph.add_node(
                node.id,
                kind=node.kind,
                name=node.name,
                file_path=node.file_path,
                line_start=node.line_start,
                line_end=node.line_end,
                complexity=node.complexity,
                cohesion=node.cohesion,
                properties=node.properties,
            )

        self._resolve_and_add_edges(edges)

    def update_file(
        self, file_path: str, new_nodes: list[CodeNode], new_edges: list[CodeEdge]
    ) -> None:
        """Incremental update: remove everything from file_path, then add new data."""
        self.remove_file(file_path)

        for node in new_nodes:
            self._graph.add_node(
                node.id,
                kind=node.kind,
                name=node.name,
                file_path=node.file_path,
                line_start=node.line_start,
                line_end=node.line_end,
                complexity=node.complexity,
                cohesion=node.cohesion,
                properties=node.properties,
            )

        self._resolve_and_add_edges(new_edges)
        self._invalidate_cache()

    def remove_file(self, file_path: str) -> None:
        """Remove all nodes (and their incident edges) belonging to a file."""
        nodes_to_remove = [
            nid
            for nid, data in self._graph.nodes(data=True)
            if data.get("file_path") == file_path
        ]
        if nodes_to_remove:
            self._graph.remove_nodes_from(nodes_to_remove)
            self._invalidate_cache()

    def _resolve_and_add_edges(self, edges: list[CodeEdge]) -> None:
        """Resolve symbolic edge targets to node IDs and add to graph.

        Edges where the source is a known node ID are processed.  The target
        may be a node ID (already in the graph) *or* a symbolic name such as
        ``"logging"`` or ``"self.method_name"``.  We build a name-to-ID lookup
        so that intra-project calls and imports resolve to real graph edges.
        Anything that cannot be resolved is recorded as an external dependency.
        """
        known_ids = set(self._graph.nodes)

        # Build name -> node-id lookup for resolution
        name_to_ids: dict[str, list[str]] = {}
        for nid, data in self._graph.nodes(data=True):
            name = data.get("name", "")
            if name:
                name_to_ids.setdefault(name, []).append(nid)

        for edge in edges:
            if edge.source not in known_ids:
                continue

            # Try direct ID match first
            if edge.target in known_ids:
                self._graph.add_edge(
                    edge.source, edge.target,
                    kind=edge.kind, weight=edge.weight, properties=edge.properties,
                )
                continue

            # Try name resolution
            target_name = edge.target
            # Strip "self." prefix for method calls
            if target_name.startswith("self."):
                target_name = target_name[5:]
            # Strip module prefixes for dotted names (e.g. "os.path.join" -> "join")
            if "." in target_name:
                parts = target_name.split(".")
                target_name = parts[-1]

            candidates = name_to_ids.get(target_name, [])
            if candidates:
                # Prefer candidate in the same file as source
                source_fp = self._graph.nodes[edge.source].get("file_path", "")
                best = None
                for cid in candidates:
                    if self._graph.nodes[cid].get("file_path", "") == source_fp:
                        best = cid
                        break
                if best is None:
                    best = candidates[0]

                self._graph.add_edge(
                    edge.source, best,
                    kind=edge.kind, weight=edge.weight, properties=edge.properties,
                )
            else:
                # External dependency — record on source node
                ext_deps = self._graph.nodes[edge.source].get("_external_deps", [])
                ext_deps.append({
                    "target": edge.target,
                    "kind": edge.kind,
                    "weight": edge.weight,
                })
                self._graph.nodes[edge.source]["_external_deps"] = ext_deps

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    @property
    def mean_coupling(self) -> float:
        """Mean coupling score across all file-level nodes."""
        file_nodes = [
            nid
            for nid, data in self._graph.nodes(data=True)
            if data.get("kind") == "file"
        ]
        if not file_nodes:
            return 0.0
        scores = [self.coupling_score(nid) for nid in file_nodes]
        return sum(scores) / len(scores)

    @property
    def mean_cohesion(self) -> float:
        """Mean cohesion score across all file-level nodes."""
        file_nodes = [
            nid
            for nid, data in self._graph.nodes(data=True)
            if data.get("kind") == "file"
        ]
        if not file_nodes:
            return 0.0
        file_paths = [self._graph.nodes[nid].get("file_path", "") for nid in file_nodes]
        scores = [self.cohesion_score(fp) for fp in file_paths]
        return sum(scores) / len(scores)

    # ------------------------------------------------------------------
    # Graph metric computations (cached)
    # ------------------------------------------------------------------

    def degree_centrality(self) -> dict[str, float]:
        cached = self._get_cached("degree_centrality")
        if cached is not None:
            return cached
        if self._graph.number_of_nodes() == 0:
            return self._set_cached("degree_centrality", {})
        return self._set_cached("degree_centrality", nx.degree_centrality(self._graph))

    def betweenness_centrality(self) -> dict[str, float]:
        cached = self._get_cached("betweenness_centrality")
        if cached is not None:
            return cached
        if self._graph.number_of_nodes() == 0:
            return self._set_cached("betweenness_centrality", {})
        return self._set_cached(
            "betweenness_centrality", nx.betweenness_centrality(self._graph)
        )

    def clustering_coefficient(self) -> dict[str, float]:
        cached = self._get_cached("clustering_coefficient")
        if cached is not None:
            return cached
        if self._graph.number_of_nodes() == 0:
            return self._set_cached("clustering_coefficient", {})
        undirected = self._graph.to_undirected()
        return self._set_cached("clustering_coefficient", nx.clustering(undirected))

    def cohesion_score(self, module_id: str) -> float:
        """Ratio of internal edges to total edges for nodes in the given module (file).

        Internal = both endpoints belong to the same file_path.
        Returns 0.0-1.0. Higher means better encapsulated.
        """
        cache_key = f"cohesion:{module_id}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        module_nodes = set(self.get_module_nodes(module_id))
        if not module_nodes:
            return self._set_cached(cache_key, 0.0)

        internal = 0
        external_graph = 0
        for nid in module_nodes:
            for _, target in self._graph.out_edges(nid):
                if target in module_nodes:
                    internal += 1
                else:
                    external_graph += 1
            for source, _ in self._graph.in_edges(nid):
                if source in module_nodes:
                    pass  # Already counted in out_edges
                else:
                    external_graph += 1
            # Count external deps (imports to outside-graph modules)
            ext_deps = self._graph.nodes[nid].get("_external_deps", [])
            external_graph += len(ext_deps)

        total = internal + external_graph
        if total == 0:
            # No dependencies at all — perfectly cohesive (isolated module)
            return self._set_cached(cache_key, 1.0)

        return self._set_cached(cache_key, internal / total if total > 0 else 1.0)

    def coupling_score(self, module_id: str) -> float:
        """Weighted sum of external dependency edge weights for a module.

        module_id can be either a file_path string or a file-node ID.
        External = at least one endpoint outside the module's file.
        """
        cache_key = f"coupling:{module_id}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        # Resolve module_id: could be a file_path or a node ID
        module_nodes = set(self.get_module_nodes(module_id))
        if not module_nodes:
            # Try treating module_id as a node ID and get its file_path
            data = self._graph.nodes.get(module_id)
            if data:
                fp = data.get("file_path", "")
                module_nodes = set(self.get_module_nodes(fp))

        if not module_nodes:
            return self._set_cached(cache_key, 0.0)

        score = 0.0
        for nid in module_nodes:
            # Count graph edges to nodes outside this module
            for _, target, edata in self._graph.out_edges(nid, data=True):
                if target not in module_nodes:
                    score += edata.get("weight", 1.0)
            for source, _, edata in self._graph.in_edges(nid, data=True):
                if source not in module_nodes:
                    score += edata.get("weight", 1.0)
            # Count external dependencies (imports to modules outside the graph)
            ext_deps = self._graph.nodes[nid].get("_external_deps", [])
            for dep in ext_deps:
                score += dep.get("weight", 1.0)

        return self._set_cached(cache_key, score)

    def stress_score(self, component_id: str) -> float:
        """Coupling / cohesion for the component's module.

        Returns float('inf') if cohesion is 0.
        """
        cache_key = f"stress:{component_id}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        data = self._graph.nodes.get(component_id)
        if data is None:
            return self._set_cached(cache_key, 0.0)

        file_path = data.get("file_path", "")
        coh = self.cohesion_score(file_path)
        coup = self.coupling_score(file_path)

        if coh == 0.0:
            result = float("inf") if coup > 0.0 else 0.0
        else:
            result = coup / coh

        return self._set_cached(cache_key, result)

    def cascade_depth(self, component_id: str) -> int:
        """BFS from component — count how many unique nodes are reachable through dependency edges."""
        if component_id not in self._graph:
            return 0

        visited: set[str] = set()
        queue: deque[str] = deque([component_id])
        visited.add(component_id)

        while queue:
            current = queue.popleft()
            for _, target, edata in self._graph.out_edges(current, data=True):
                if target not in visited:
                    visited.add(target)
                    queue.append(target)

        # Exclude the start node itself from the count
        return len(visited) - 1

    def get_high_stress_components(
        self, threshold: float = 0.7
    ) -> list[tuple[str, float]]:
        """Returns (node_id, stress) sorted descending for all components above threshold."""
        results: list[tuple[str, float]] = []
        for nid in self._graph.nodes:
            stress = self.stress_score(nid)
            if stress > threshold:
                results.append((nid, stress))
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def get_module_nodes(self, file_path: str) -> list[str]:
        """All node IDs belonging to a file."""
        return [
            nid
            for nid, data in self._graph.nodes(data=True)
            if data.get("file_path") == file_path
        ]

    def impact_radius(
        self, component_id: str, threshold: float = 0.1
    ) -> set[str]:
        """Set of node IDs reachable from component where edge coupling weight > threshold."""
        if component_id not in self._graph:
            return set()

        visited: set[str] = set()
        queue: deque[str] = deque([component_id])
        visited.add(component_id)

        while queue:
            current = queue.popleft()
            for _, target, edata in self._graph.out_edges(current, data=True):
                if target not in visited and edata.get("weight", 1.0) > threshold:
                    visited.add(target)
                    queue.append(target)

        # Remove the start node — we want the radius, not the origin
        visited.discard(component_id)
        return visited
