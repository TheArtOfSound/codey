"""NFET controller state objects.

These dataclasses are intentionally lightweight so they can move through the
API layer, router, and autonomous worker paths without dragging in database or
framework concerns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def _finite_state_float(value: float, fallback: float = 0.0) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return normalized if math.isfinite(normalized) else fallback


@dataclass(frozen=True)
class NodeState:
    """Per-component NFET state.

    Components are currently file-level nodes. The file unit is deliberate for
    Phase 1 because the rest of the product already reasons mostly at the file
    boundary for prompting, repo scans, and autonomous interventions.
    """

    node_id: str
    name: str
    file_path: str
    kind: str
    stress: float
    coupling: float
    cohesion: float
    complexity: float
    cascade_depth: int
    impact_radius: int
    fanin: int
    fanout: int
    betweenness: float
    shared_state_edges: int
    cycle_detected: bool
    sigma: float
    kappa: float
    gamma: float
    es: float
    risk_score: float
    risk_level: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "file_path": self.file_path,
            "kind": self.kind,
            "stress": _finite_state_float(self.stress),
            "coupling": _finite_state_float(self.coupling),
            "cohesion": _finite_state_float(self.cohesion),
            "complexity": _finite_state_float(self.complexity),
            "cascade_depth": self.cascade_depth,
            "impact_radius": self.impact_radius,
            "fanin": self.fanin,
            "fanout": self.fanout,
            "betweenness": _finite_state_float(self.betweenness),
            "shared_state_edges": self.shared_state_edges,
            "cycle_detected": self.cycle_detected,
            "sigma": _finite_state_float(self.sigma),
            "kappa": _finite_state_float(self.kappa),
            "gamma": _finite_state_float(self.gamma),
            "es": _finite_state_float(self.es),
            "risk_score": _finite_state_float(self.risk_score),
            "risk_level": self.risk_level,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class RepoState:
    """Repo-wide NFET operating state."""

    phase: str
    global_kappa: float
    global_sigma: float
    global_es: float
    total_nodes: int
    total_edges: int
    highest_stress_component: str
    highest_stress_value: float
    components: list[NodeState] = field(default_factory=list)
    hotspots: list[NodeState] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "global_kappa": _finite_state_float(self.global_kappa),
            "global_sigma": _finite_state_float(self.global_sigma),
            "global_es": _finite_state_float(self.global_es),
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "highest_stress_component": self.highest_stress_component,
            "highest_stress_value": _finite_state_float(self.highest_stress_value),
            "components": [component.to_dict() for component in self.components],
            "hotspots": [hotspot.to_dict() for hotspot in self.hotspots],
        }


@dataclass(frozen=True)
class ActionCandidate:
    """A ranked intervention candidate selected by the NFET controller."""

    candidate_id: str
    kind: str
    title: str
    description: str
    target_node_id: str
    target_file_path: str
    predicted_repo_es_delta: float
    predicted_sigma_reduction: float
    predicted_kappa_reduction: float
    risk: float
    cost: float
    reversibility: float
    confidence: float
    score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "target_node_id": self.target_node_id,
            "target_file_path": self.target_file_path,
            "predicted_repo_es_delta": _finite_state_float(
                self.predicted_repo_es_delta
            ),
            "predicted_sigma_reduction": _finite_state_float(
                self.predicted_sigma_reduction
            ),
            "predicted_kappa_reduction": _finite_state_float(
                self.predicted_kappa_reduction
            ),
            "risk": _finite_state_float(self.risk),
            "cost": _finite_state_float(self.cost),
            "reversibility": _finite_state_float(self.reversibility),
            "confidence": _finite_state_float(self.confidence),
            "score": _finite_state_float(self.score),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ActionSimulation:
    """Predicted effect of a candidate intervention."""

    candidate: ActionCandidate
    before_component_es: float
    after_component_es: float
    before_repo_es: float
    after_repo_es: float
    predicted_phase: str
    narrative: str

    def to_dict(self) -> dict:
        return {
            "candidate": self.candidate.to_dict(),
            "before_component_es": _finite_state_float(self.before_component_es),
            "after_component_es": _finite_state_float(self.after_component_es),
            "before_repo_es": _finite_state_float(self.before_repo_es),
            "after_repo_es": _finite_state_float(self.after_repo_es),
            "predicted_phase": self.predicted_phase,
            "narrative": self.narrative,
        }
