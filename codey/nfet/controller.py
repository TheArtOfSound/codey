"""NFET controller.

Phase 1 uses NFET as an intervention-ranking layer, not just a reporting pass.
The controller scores file-level hotspots, ranks candidate interventions, and
provides prompt/router guidance that the rest of the product can consume.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import TYPE_CHECKING

from codey.nfet.state import ActionCandidate, ActionSimulation, NodeState, RepoState
from codey.nfet.sweep import NFETSweep, Phase

if TYPE_CHECKING:
    from codey.graph.engine import CodebaseGraph


@dataclass(frozen=True)
class ControllerWeights:
    """Tunable weights for Phase 1 intervention ranking."""

    sigma_stress: float = 0.35
    sigma_cohesion: float = 0.2
    sigma_cascade: float = 0.2
    sigma_complexity: float = 0.15
    sigma_cycle: float = 0.1
    kappa_coupling: float = 0.35
    kappa_fanin: float = 0.15
    kappa_fanout: float = 0.2
    kappa_betweenness: float = 0.2
    kappa_shared_state: float = 0.1
    gamma_goal: float = 0.45
    gamma_priority: float = 0.25
    gamma_urgency: float = 0.2
    gamma_focus: float = 0.1
    risk_penalty: float = 0.45
    cost_penalty: float = 0.25
    reversibility_bonus: float = 0.2
    goal_bonus: float = 0.15
    kappa_excess_lambda: float = 1.4


class NFETController:
    """Turns graph metrics into ranked interventions and prompt guidance."""

    def __init__(
        self,
        sweep_engine: NFETSweep | None = None,
        weights: ControllerWeights | None = None,
    ) -> None:
        self.sweep_engine = sweep_engine or NFETSweep()
        self.weights = weights or ControllerWeights()

    def analyze(
        self,
        graph: CodebaseGraph,
        goal: str | None = None,
        target_file: str | None = None,
        top_k: int = 5,
    ) -> RepoState:
        """Compute repo and per-component operating state."""
        sweep = self.sweep_engine.run(graph)
        file_nodes = graph.file_nodes()
        if not file_nodes:
            return RepoState(
                phase="unknown",
                global_kappa=0.0,
                global_sigma=0.0,
                global_es=0.5,
                total_nodes=sweep.total_nodes,
                total_edges=sweep.total_edges,
                highest_stress_component="",
                highest_stress_value=0.0,
                components=[],
                hotspots=[],
            )

        betweenness = graph.betweenness_centrality()

        coupling_map = {node_id: graph.coupling_score(node_id) for node_id in file_nodes}
        complexity_map: dict[str, float] = {}
        cascade_map: dict[str, int] = {}
        impact_map: dict[str, int] = {}
        stats_map: dict[str, dict[str, int]] = {}
        cycle_map: dict[str, bool] = {}
        stress_map: dict[str, float] = {}

        for node_id in file_nodes:
            data = graph.node_data(node_id)
            file_path = data.get("file_path", "")
            complexity_map[node_id] = graph.module_complexity(file_path)
            cascade_map[node_id] = graph.cascade_depth(node_id)
            impact_map[node_id] = len(graph.impact_radius(node_id))
            stats_map[node_id] = graph.module_dependency_stats(node_id)
            cycle_map[node_id] = bool(graph.cycles_for_component(node_id))
            stress_raw = graph.stress_score(node_id)
            stress_map[node_id] = self._normalize_stress(stress_raw)

        max_coupling = max(coupling_map.values(), default=1.0) or 1.0
        max_complexity = max(complexity_map.values(), default=1.0) or 1.0
        max_cascade = max(cascade_map.values(), default=1) or 1
        max_impact = max(impact_map.values(), default=1) or 1
        max_fanin = max((stats["fanin"] for stats in stats_map.values()), default=1) or 1
        max_fanout = max((stats["fanout"] for stats in stats_map.values()), default=1) or 1
        max_shared_state = max(
            (stats["shared_state_edges"] for stats in stats_map.values()),
            default=1,
        ) or 1
        max_betweenness = max((betweenness.get(node_id, 0.0) for node_id in file_nodes), default=1.0) or 1.0

        component_states: list[NodeState] = []
        for node_id in file_nodes:
            data = graph.node_data(node_id)
            file_path = data.get("file_path", "")
            cohesion = graph.cohesion_score(file_path)
            coupling_norm = self._clamp(coupling_map[node_id] / max_coupling)
            complexity_norm = self._clamp(complexity_map[node_id] / max_complexity)
            cascade_norm = self._clamp(cascade_map[node_id] / max_cascade)
            impact_norm = self._clamp(impact_map[node_id] / max_impact)
            fanin_norm = self._clamp(stats_map[node_id]["fanin"] / max_fanin)
            fanout_norm = self._clamp(stats_map[node_id]["fanout"] / max_fanout)
            shared_state_norm = self._clamp(
                stats_map[node_id]["shared_state_edges"] / max_shared_state
            )
            betweenness_norm = self._clamp(
                betweenness.get(node_id, 0.0) / max_betweenness
            )
            cycle_norm = 1.0 if cycle_map[node_id] else 0.0
            stress_norm = stress_map[node_id]
            goal_alignment = self._goal_alignment(
                goal=goal,
                target_file=target_file,
                file_path=file_path,
                component_name=data.get("name", node_id),
            )
            focus_bonus = 1.0 if target_file and file_path == target_file else 0.0
            priority = max(stress_norm, coupling_norm, impact_norm)
            urgency = self._urgency(
                phase=sweep.phase,
                is_top=node_id == sweep.highest_stress_component,
                stress=stress_norm,
            )

            sigma = self._clamp(
                self.weights.sigma_stress * stress_norm
                + self.weights.sigma_cohesion * (1.0 - self._clamp(cohesion))
                + self.weights.sigma_cascade * cascade_norm
                + self.weights.sigma_complexity * complexity_norm
                + self.weights.sigma_cycle * cycle_norm
            )
            kappa = self._clamp(
                self.weights.kappa_coupling * coupling_norm
                + self.weights.kappa_fanin * fanin_norm
                + self.weights.kappa_fanout * fanout_norm
                + self.weights.kappa_betweenness * betweenness_norm
                + self.weights.kappa_shared_state * shared_state_norm
            )
            gamma = self._clamp(
                self.weights.gamma_goal * goal_alignment
                + self.weights.gamma_priority * priority
                + self.weights.gamma_urgency * urgency
                + self.weights.gamma_focus * focus_bonus
            )

            coherence = max(0.1, self._clamp(cohesion) * (1.0 - 0.35 * kappa))
            recoverability = max(
                0.1,
                1.0 - (0.4 * cascade_norm + 0.35 * impact_norm + 0.25 * cycle_norm),
            )
            kappa_excess = max(0.0, kappa - 0.55)
            es = self._clamp(
                (max(gamma, 0.15) * coherence * recoverability)
                / (1.0 + sigma + self.weights.kappa_excess_lambda * kappa_excess)
            )

            risk_score = self._clamp(0.45 * sigma + 0.35 * kappa + 0.2 * cascade_norm)
            risk_level = self._risk_level(risk_score)
            reasons = self._build_reasons(
                stress=stress_norm,
                cohesion=cohesion,
                coupling=coupling_map[node_id],
                cascade_depth=cascade_map[node_id],
                fanout=stats_map[node_id]["fanout"],
                shared_state_edges=stats_map[node_id]["shared_state_edges"],
                cycle_detected=cycle_map[node_id],
                goal_alignment=goal_alignment,
            )

            component_states.append(
                NodeState(
                    node_id=node_id,
                    name=data.get("name", node_id),
                    file_path=file_path,
                    kind=data.get("kind", "file"),
                    stress=stress_norm,
                    coupling=coupling_map[node_id],
                    cohesion=cohesion,
                    complexity=complexity_map[node_id],
                    cascade_depth=cascade_map[node_id],
                    impact_radius=impact_map[node_id],
                    fanin=stats_map[node_id]["fanin"],
                    fanout=stats_map[node_id]["fanout"],
                    betweenness=betweenness.get(node_id, 0.0),
                    shared_state_edges=stats_map[node_id]["shared_state_edges"],
                    cycle_detected=cycle_map[node_id],
                    sigma=sigma,
                    kappa=kappa,
                    gamma=gamma,
                    es=es,
                    risk_score=risk_score,
                    risk_level=risk_level,
                    reasons=reasons,
                )
            )

        component_states.sort(key=lambda state: (state.es, -state.gamma, -state.risk_score))
        hotspots = [
            state
            for state in component_states
            if state.risk_score >= 0.45 or (target_file and state.file_path == target_file)
        ]
        if not hotspots:
            hotspots = component_states[:top_k]

        return RepoState(
            phase=sweep.phase.value,
            global_kappa=sweep.kappa,
            global_sigma=sweep.sigma,
            global_es=sweep.es_score,
            total_nodes=sweep.total_nodes,
            total_edges=sweep.total_edges,
            highest_stress_component=sweep.highest_stress_component,
            highest_stress_value=sweep.highest_stress_value,
            components=component_states,
            hotspots=hotspots[:top_k],
        )

    def rank_interventions(
        self,
        graph: CodebaseGraph,
        goal: str | None = None,
        target_file: str | None = None,
        limit: int = 5,
        repo_state: RepoState | None = None,
        focus_component: str | None = None,
    ) -> list[ActionCandidate]:
        """Rank intervention candidates by predicted ES improvement."""
        repo_state = repo_state or self.analyze(
            graph,
            goal=goal,
            target_file=target_file,
            top_k=max(limit, 5),
        )
        components = repo_state.hotspots or repo_state.components
        if focus_component:
            resolved = self.resolve_component_id(graph, focus_component)
            if resolved:
                components = [
                    component
                    for component in repo_state.components
                    if component.node_id == resolved
                ] or components

        candidates: list[ActionCandidate] = []
        for component in components:
            candidates.extend(self._build_component_candidates(component, repo_state))

        deduped: dict[tuple[str, str], ActionCandidate] = {}
        for candidate in candidates:
            key = (candidate.kind, candidate.target_node_id)
            previous = deduped.get(key)
            if previous is None or candidate.score > previous.score:
                deduped[key] = candidate

        ranked = sorted(
            deduped.values(),
            key=lambda candidate: candidate.score,
            reverse=True,
        )
        return ranked[:limit]

    def simulate_action(
        self,
        graph: CodebaseGraph,
        candidate: ActionCandidate,
        goal: str | None = None,
        target_file: str | None = None,
        repo_state: RepoState | None = None,
    ) -> ActionSimulation:
        """Predict the effect of a candidate without mutating the graph."""
        repo_state = repo_state or self.analyze(graph, goal=goal, target_file=target_file)
        target = self._find_component(repo_state, candidate.target_node_id)
        if target is None:
            raise ValueError(f"Unknown NFET component '{candidate.target_node_id}'")

        before_component_es = target.es
        after_component_es = self._clamp(
            target.es
            + candidate.predicted_repo_es_delta * (0.65 + 0.35 * target.gamma)
        )
        before_repo_es = repo_state.global_es
        after_repo_es = self._clamp(before_repo_es + candidate.predicted_repo_es_delta)
        predicted_phase = self._phase_for(after_repo_es).value
        narrative = (
            f"{candidate.title} is predicted to raise repo ES from "
            f"{before_repo_es:.3f} to {after_repo_es:.3f} by reducing instability "
            f"in {target.file_path}. Expected reductions: "
            f"sigma {candidate.predicted_sigma_reduction:.3f}, "
            f"kappa {candidate.predicted_kappa_reduction:.3f}."
        )

        return ActionSimulation(
            candidate=candidate,
            before_component_es=before_component_es,
            after_component_es=after_component_es,
            before_repo_es=before_repo_es,
            after_repo_es=after_repo_es,
            predicted_phase=predicted_phase,
            narrative=narrative,
        )

    def build_guidance(
        self,
        repo_state: RepoState,
        candidates: list[ActionCandidate],
        limit: int = 3,
    ) -> str:
        """Build a concise prompt block for NFET-aware planning."""
        hotspot_lines = []
        for hotspot in repo_state.hotspots[:limit]:
            hotspot_lines.append(
                f"- {hotspot.file_path}: sigma={hotspot.sigma:.2f}, "
                f"kappa={hotspot.kappa:.2f}, ES={hotspot.es:.2f}, "
                f"risk={hotspot.risk_level}; {hotspot.reasons[0] if hotspot.reasons else 'monitor closely'}"
            )

        candidate_lines = []
        for candidate in candidates[:limit]:
            candidate_lines.append(
                f"- {candidate.title}: score={candidate.score:.2f}, "
                f"delta_ES={candidate.predicted_repo_es_delta:.3f}, "
                f"target={candidate.target_file_path}"
            )

        lines = [
            "NFET OPERATING CONTEXT:",
            (
                "No supported source files were parsed for NFET scoring. "
                "Treat this as repository context only and ground any change in the actual repo files."
                if not repo_state.components
                else (
                    f"Global phase={repo_state.phase}, ES={repo_state.global_es:.3f}, "
                    f"kappa={repo_state.global_kappa:.3f}, sigma={repo_state.global_sigma:.3f}"
                )
            ),
            "Priority hotspots:",
            *(hotspot_lines or ["- No urgent hotspots detected."]),
            "Preferred intervention order:",
            *(candidate_lines or ["- No intervention pressure; preserve current structure."]),
            "Decision rule: prefer actions that raise ES and avoid new dependencies into hotspot files.",
        ]
        return "\n".join(lines)

    def build_router_context(self, repo_state: RepoState) -> dict[str, float | int | str]:
        """Compact NFET context for model routing."""
        top_hotspot = repo_state.hotspots[0] if repo_state.hotspots else None
        return {
            "nfet_phase": repo_state.phase,
            "nfet_hotspots": len(repo_state.hotspots),
            "nfet_focus_risk": top_hotspot.risk_score if top_hotspot else 0.0,
            "nfet_goal_pressure": top_hotspot.gamma if top_hotspot else 0.0,
            "codebase_files": len(repo_state.components),
            "codebase_tokens": max(repo_state.total_nodes * 120, 2048),
        }

    def resolve_component_id(
        self,
        graph: CodebaseGraph,
        component_ref: str | None,
    ) -> str | None:
        """Resolve an arbitrary component reference to the owning file node."""
        if not component_ref:
            return None
        if component_ref in graph._graph:
            data = graph.node_data(component_ref)
            if data.get("kind") == "file":
                return component_ref
            file_path = data.get("file_path", "")
            return graph.find_file_node(file_path)

        file_node = graph.find_file_node(component_ref)
        if file_node:
            return file_node

        normalized = component_ref.lower()
        for node_id in graph.file_nodes():
            data = graph.node_data(node_id)
            name = str(data.get("name", "")).lower()
            file_path = str(data.get("file_path", "")).lower()
            if normalized == name or normalized == file_path:
                return node_id
        return None

    def _build_component_candidates(
        self,
        component: NodeState,
        repo_state: RepoState,
    ) -> list[ActionCandidate]:
        candidates: list[ActionCandidate] = []

        if component.cycle_detected:
            candidates.append(
                self._make_candidate(
                    component=component,
                    kind="break_cycle",
                    title=f"Break dependency cycle in {component.file_path}",
                    description=(
                        "Untangle cycle-causing imports or callbacks and insert a boundary "
                        "so changes stop propagating through the same loop."
                    ),
                    delta_es=0.12 + 0.08 * component.kappa,
                    sigma_reduction=0.08 + 0.08 * component.sigma,
                    kappa_reduction=0.1 + 0.08 * component.kappa,
                    risk=0.52,
                    cost=0.58,
                    reversibility=0.45,
                    confidence=0.78,
                    reasons=["Cycle detected in a high-risk component."],
                )
            )

        if component.kappa >= 0.55 or component.fanout >= 4:
            candidates.append(
                self._make_candidate(
                    component=component,
                    kind="introduce_boundary",
                    title=f"Introduce boundary around {component.file_path}",
                    description=(
                        "Insert an interface or service boundary before adding more direct "
                        "cross-module dependencies."
                    ),
                    delta_es=0.08 + 0.1 * component.gamma,
                    sigma_reduction=0.05 + 0.05 * component.sigma,
                    kappa_reduction=0.08 + 0.1 * component.kappa,
                    risk=0.38,
                    cost=0.42,
                    reversibility=0.6,
                    confidence=0.74,
                    reasons=["High fan-out or coupling makes this component load-bearing."],
                )
            )

        if component.sigma >= 0.55 or component.stress >= 0.65:
            candidates.append(
                self._make_candidate(
                    component=component,
                    kind="extract_module",
                    title=f"Extract hotspot logic from {component.file_path}",
                    description=(
                        "Move unstable or mixed-responsibility logic into a smaller module "
                        "to reduce local stress before new feature work."
                    ),
                    delta_es=0.09 + 0.1 * component.sigma,
                    sigma_reduction=0.1 + 0.08 * component.sigma,
                    kappa_reduction=0.05 + 0.06 * component.kappa,
                    risk=0.44,
                    cost=0.5,
                    reversibility=0.5,
                    confidence=0.76,
                    reasons=["Instability is high enough that local extraction is safer than adding more code in place."],
                )
            )

        if component.cohesion <= 0.45 or component.complexity >= 6.0:
            candidates.append(
                self._make_candidate(
                    component=component,
                    kind="split_component",
                    title=f"Split responsibilities in {component.file_path}",
                    description=(
                        "Separate mixed responsibilities inside the file so cohesion improves "
                        "and changes stop spanning unrelated behaviors."
                    ),
                    delta_es=0.07 + 0.05 * component.sigma,
                    sigma_reduction=0.07 + 0.05 * component.sigma,
                    kappa_reduction=0.04 + 0.04 * component.kappa,
                    risk=0.35,
                    cost=0.46,
                    reversibility=0.62,
                    confidence=0.71,
                    reasons=["Low cohesion or rising complexity is making the file harder to change safely."],
                )
            )

        if component.shared_state_edges > 0:
            candidates.append(
                self._make_candidate(
                    component=component,
                    kind="reduce_shared_state",
                    title=f"Reduce shared state in {component.file_path}",
                    description=(
                        "Isolate mutable state behind explicit boundaries or immutable value flow "
                        "to reduce cascade risk."
                    ),
                    delta_es=0.06 + 0.06 * component.kappa,
                    sigma_reduction=0.05 + 0.04 * component.sigma,
                    kappa_reduction=0.05 + 0.05 * component.kappa,
                    risk=0.28,
                    cost=0.34,
                    reversibility=0.72,
                    confidence=0.82,
                    reasons=["Shared state edges make regressions more likely and harder to reverse."],
                )
            )

        if repo_state.phase == Phase.CRITICAL.value and component.risk_score >= 0.6:
            candidates.append(
                self._make_candidate(
                    component=component,
                    kind="stabilize_hotspot",
                    title=f"Stabilize {component.file_path} before feature work",
                    description=(
                        "Freeze net-new coupling here and prioritize small, reversible changes "
                        "that raise recoverability."
                    ),
                    delta_es=0.1,
                    sigma_reduction=0.06,
                    kappa_reduction=0.04,
                    risk=0.24,
                    cost=0.22,
                    reversibility=0.85,
                    confidence=0.79,
                    reasons=["Global NFET phase is critical, so stabilization should beat feature expansion."],
                )
            )

        return candidates

    def _make_candidate(
        self,
        component: NodeState,
        kind: str,
        title: str,
        description: str,
        delta_es: float,
        sigma_reduction: float,
        kappa_reduction: float,
        risk: float,
        cost: float,
        reversibility: float,
        confidence: float,
        reasons: list[str],
    ) -> ActionCandidate:
        predicted_repo_es_delta = self._clamp(delta_es * (0.7 + 0.3 * component.gamma))
        risk = self._clamp(risk + 0.15 * component.risk_score)
        cost = self._clamp(cost + 0.1 * min(component.impact_radius / 10, 1.0))
        reversibility = self._clamp(reversibility)
        confidence = self._clamp(confidence)
        score = (
            predicted_repo_es_delta
            - self.weights.risk_penalty * risk
            - self.weights.cost_penalty * cost
            + self.weights.reversibility_bonus * reversibility
            + self.weights.goal_bonus * component.gamma
        )
        raw_id = f"{kind}:{component.node_id}:{component.file_path}"
        candidate_id = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:12]
        return ActionCandidate(
            candidate_id=candidate_id,
            kind=kind,
            title=title,
            description=description,
            target_node_id=component.node_id,
            target_file_path=component.file_path,
            predicted_repo_es_delta=predicted_repo_es_delta,
            predicted_sigma_reduction=self._clamp(sigma_reduction),
            predicted_kappa_reduction=self._clamp(kappa_reduction),
            risk=risk,
            cost=cost,
            reversibility=reversibility,
            confidence=confidence,
            score=score,
            reasons=list(reasons),
        )

    @staticmethod
    def _normalize_stress(raw_stress: float) -> float:
        try:
            raw_stress = float(raw_stress)
        except (OverflowError, TypeError, ValueError):
            raw_stress = 1e6
        if not math.isfinite(raw_stress):
            raw_stress = 1e6
        return raw_stress / (raw_stress + 10.0) if raw_stress > 0 else 0.0

    @staticmethod
    def _tokenize(text: str | None) -> set[str]:
        if not text:
            return set()
        return {
            token
            for token in re.findall(r"[a-zA-Z0-9_./-]+", text.lower())
            if len(token) > 2
        }

    def _goal_alignment(
        self,
        goal: str | None,
        target_file: str | None,
        file_path: str,
        component_name: str,
    ) -> float:
        if target_file and target_file == file_path:
            return 1.0

        goal_tokens = self._tokenize(goal)
        if not goal_tokens:
            return 0.2

        component_tokens = self._tokenize(f"{file_path} {component_name}")
        overlap = len(goal_tokens & component_tokens)
        return self._clamp(overlap / max(len(goal_tokens), 1))

    @staticmethod
    def _urgency(phase: Phase, is_top: bool, stress: float) -> float:
        base = {
            Phase.RIDGE: 0.25,
            Phase.CAUTION: 0.55,
            Phase.CRITICAL: 0.9,
        }.get(phase, 0.4)
        if is_top:
            base += 0.1
        if stress > 0.7:
            base += 0.1
        return min(base, 1.0)

    @staticmethod
    def _build_reasons(
        *,
        stress: float,
        cohesion: float,
        coupling: float,
        cascade_depth: int,
        fanout: int,
        shared_state_edges: int,
        cycle_detected: bool,
        goal_alignment: float,
    ) -> list[str]:
        reasons: list[str] = []
        if stress >= 0.6:
            reasons.append(f"normalized stress is elevated ({stress:.2f})")
        if cohesion <= 0.45:
            reasons.append(f"cohesion is weak ({cohesion:.2f})")
        if coupling > 0:
            reasons.append(f"external coupling is high ({coupling:.1f})")
        if cascade_depth >= 3:
            reasons.append(f"cascade depth reaches {cascade_depth} downstream components")
        if fanout >= 4:
            reasons.append(f"fan-out is high ({fanout})")
        if shared_state_edges > 0:
            reasons.append(f"shared-state edges detected ({shared_state_edges})")
        if cycle_detected:
            reasons.append("dependency cycle detected")
        if goal_alignment >= 0.5:
            reasons.append("strong alignment with the current goal")
        return reasons[:3] or ["structural review recommended"]

    @staticmethod
    def _risk_level(risk_score: float) -> str:
        if risk_score >= 0.8:
            return "critical"
        if risk_score >= 0.6:
            return "high"
        if risk_score >= 0.35:
            return "moderate"
        return "low"

    @staticmethod
    def _phase_for(es_score: float) -> Phase:
        if es_score > 0.7:
            return Phase.RIDGE
        if es_score > 0.4:
            return Phase.CAUTION
        return Phase.CRITICAL

    @staticmethod
    def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
        try:
            normalized = float(value)
        except (OverflowError, TypeError, ValueError):
            return lower
        if not math.isfinite(normalized):
            return lower
        return max(lower, min(upper, normalized))

    @staticmethod
    def _find_component(repo_state: RepoState, node_id: str) -> NodeState | None:
        for component in repo_state.components:
            if component.node_id == node_id:
                return component
        return None
