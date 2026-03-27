"""PromptBuilder — constructs structurally-aware prompts for the LLM using live NFET data."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codey.graph.engine import CodebaseGraph
    from codey.nfet.sweep import NFETSweep

from codey.nfet.sweep import Phase, SweepResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Codey, a network-aware coding AI. You have full visibility into "
    "the structural health of this codebase via NFET analysis. When writing "
    "code, consider both correctness AND structural impact. Avoid adding "
    "dependencies to high-stress components. Prefer solutions that keep the "
    "codebase within its stability ridge."
)

_PHASE_DESCRIPTIONS = {
    Phase.RIDGE: "Stable — the codebase is within its equilibrium ridge. Safe to make changes.",
    Phase.CAUTION: "Drifting — structural metrics are outside the optimal range. Proceed carefully.",
    Phase.CRITICAL: "Critical — the codebase is structurally degraded. Minimize coupling changes.",
}

_STRESS_THRESHOLDS = {"low": 0.3, "high": 0.7}


class PromptBuilder:
    """Constructs structurally-aware prompts backed by a live CodebaseGraph and NFETSweep engine."""

    def __init__(self, graph: CodebaseGraph, sweep: NFETSweep) -> None:
        self.graph = graph
        self.sweep = sweep

    def build_system_prompt(self) -> str:
        """Return the static system prompt for Codey."""
        return SYSTEM_PROMPT

    def build_context(
        self,
        sweep_result: SweepResult,
        target_file: str | None = None,
        impact_radius_limit: int = 15,
    ) -> str:
        """Build the structural context block injected into the user message.

        Parameters
        ----------
        sweep_result:
            A recent SweepResult from NFETSweep.run().
        target_file:
            Optional file path the user intends to modify. When provided, the
            context includes per-component metrics and structural constraints.
        impact_radius_limit:
            Maximum number of components allowed in the impact radius before
            the change is flagged as too broad.
        """
        phase_desc = _PHASE_DESCRIPTIONS.get(
            sweep_result.phase,
            "Unknown phase — treat as caution.",
        )

        # --- Highest-stress component metadata ---
        hs_id = sweep_result.highest_stress_component
        hs_depth = self.graph.cascade_depth(hs_id) if hs_id else 0

        lines = [
            "CODEBASE STRUCTURAL STATE:",
            (
                f"Phase: {sweep_result.phase.value} "
                f"(ES={sweep_result.es_score:.3f}, "
                f"\u03ba={sweep_result.kappa:.3f}, "
                f"\u03c3={sweep_result.sigma:.3f})"
            ),
            f"Ridge status: {phase_desc}",
            (
                f"Highest-stress component: {hs_id} "
                f"(stress={sweep_result.highest_stress_value:.2f}, "
                f"cascade_depth={hs_depth})"
            ),
        ]

        # --- Target component section ---
        if target_file is not None:
            lines.append("")
            target_nodes = self.graph.get_module_nodes(target_file)
            if target_nodes:
                rep_node = target_nodes[0]
                stress = self.graph.stress_score(rep_node)
                coupling = self.graph.coupling_score(target_file)
                depth = self.graph.cascade_depth(rep_node)
                bc = self.graph.betweenness_centrality().get(rep_node, 0.0)

                stress_label, stress_note = self._classify_stress(stress)
                bc_label = self._classify_centrality(bc)

                lines.extend([
                    f"TARGET COMPONENT: {target_file}",
                    f"Stress score: {stress:.2f} ({stress_label} \u2014 {stress_note})",
                    f"Coupling count: {coupling:.0f} dependencies",
                    f"Cascade depth: {depth} components affected if this fails",
                    f"Betweenness centrality: {bc:.2f} ({bc_label} load-bearing)",
                ])
            else:
                lines.extend([
                    f"TARGET COMPONENT: {target_file}",
                    "Status: New file (not yet in the graph)",
                ])

            # --- Structural constraints ---
            lines.append("")
            lines.append("STRUCTURAL CONSTRAINTS:")
            constraints = self._build_constraints(sweep_result, target_file)
            for constraint in constraints:
                lines.append(constraint)
            lines.append(f"Impact radius limit: {impact_radius_limit}")

        return "\n".join(lines)

    def build_full_prompt(
        self,
        user_request: str,
        sweep_result: SweepResult,
        target_file: str | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        """Build the system prompt and messages list for the Anthropic API.

        Returns
        -------
        tuple of (system_prompt, messages)
            system_prompt: str for the ``system`` parameter.
            messages: list of dicts suitable for the ``messages`` parameter.
        """
        context = self.build_context(sweep_result, target_file=target_file)
        system = self.build_system_prompt()
        messages = [
            {"role": "user", "content": f"{context}\n\nUSER REQUEST: {user_request}"},
        ]
        return system, messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_stress(stress: float) -> tuple[str, str]:
        """Return (label, advisory note) for a stress score."""
        if stress < _STRESS_THRESHOLDS["low"]:
            return "LOW", "safe to modify"
        if stress < _STRESS_THRESHOLDS["high"]:
            return "MODERATE", "proceed with caution"
        return "HIGH", "high risk"

    @staticmethod
    def _classify_centrality(bc: float) -> str:
        if bc < 0.1:
            return "low"
        if bc < 0.3:
            return "moderate"
        return "high"

    def _build_constraints(
        self,
        sweep_result: SweepResult,
        target_file: str,
    ) -> list[str]:
        """Generate a list of human-readable structural constraints."""
        constraints: list[str] = []

        # Warn against importing from high-stress components
        high_stress = self.graph.get_high_stress_components(threshold=0.7)
        for comp_id, stress_val in high_stress:
            comp_data = self.graph._graph.nodes.get(comp_id)
            if comp_data is None:
                continue
            comp_file = comp_data.get("file_path", comp_id)
            if comp_file == target_file:
                continue
            constraints.append(
                f"Do not add imports from {comp_file} (stress={stress_val:.2f})"
            )

        # Check if target itself is high-stress — suggest extraction
        target_nodes = self.graph.get_module_nodes(target_file)
        if target_nodes:
            rep = target_nodes[0]
            target_stress = self.graph.stress_score(rep)
            if target_stress >= _STRESS_THRESHOLDS["high"]:
                constraints.append(
                    "Preferred: extract shared logic to reduce coupling in this file"
                )

        # Phase-level guidance
        if sweep_result.phase == Phase.CRITICAL:
            constraints.append(
                "CRITICAL PHASE: Avoid any net increase in external coupling"
            )
        elif sweep_result.phase == Phase.CAUTION:
            constraints.append(
                "CAUTION PHASE: Minimize new cross-module dependencies"
            )

        if not constraints:
            constraints.append("No special constraints — codebase is healthy")

        return constraints
