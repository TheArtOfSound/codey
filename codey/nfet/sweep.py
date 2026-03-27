"""NFET parameter sweep — computes kappa, sigma, equilibrium score, and phase classification."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from codey.graph.engine import CodebaseGraph

logger = logging.getLogger(__name__)


class Phase(Enum):
    """Structural health phase of the codebase."""

    RIDGE = "ridge"
    CAUTION = "caution"
    CRITICAL = "critical"


@dataclass(frozen=True)
class SweepResult:
    """Immutable snapshot of a full NFET sweep."""

    kappa: float
    sigma: float
    es_score: float
    phase: Phase
    highest_stress_component: str
    highest_stress_value: float
    total_nodes: int
    total_edges: int
    mean_coupling: float
    mean_cohesion: float
    top_stress_components: list[tuple[str, float]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class NFETSweep:
    """Runs the full NFET parameter sweep against a CodebaseGraph.

    Parameters
    ----------
    alpha : float
        Amplitude scaling for the equilibrium score Gaussian envelope.
    beta : float
        Width parameter for the Gaussian penalty around sigma_star.
    sigma_star : float
        Optimal cascade-margin distance (centre of the Gaussian).
    kappa_star : float
        Optimal coupling density (centre of the linear penalty).
    kappa_max : float
        Maximum coupling density used for normalisation.
    """

    def __init__(
        self,
        alpha: float = 1.0,
        beta: float = 2.0,
        sigma_star: float = 0.5,
        kappa_star: float = 0.4,
        kappa_max: float = 1.0,
    ) -> None:
        self.alpha = alpha
        self.beta = beta
        self.sigma_star = sigma_star
        self.kappa_star = kappa_star
        self.kappa_max = kappa_max

    # ------------------------------------------------------------------
    # Core sweep
    # ------------------------------------------------------------------

    def run(self, graph: CodebaseGraph) -> SweepResult:
        """Execute the full NFET parameter sweep.

        Algorithm
        ---------
        1. Collect all file-level nodes.
        2. Compute kappa (mean coupling density, normalised to [0, 1]).
        3. For each component compute stress = coupling / cohesion.
        4. Compute sigma = 1 - max_stress / (max_stress + 1), always in [0, 1].
        5. ES = alpha * exp(-beta * (sigma - sigma_star)^2)
                       * (1 - |kappa - kappa_star| / kappa_max)
        6. Phase classification by ES threshold.
        """
        file_nodes = [
            nid
            for nid, data in graph._graph.nodes(data=True)
            if data.get("kind") == "file"
        ]

        total_nodes = graph.node_count
        total_edges = graph.edge_count

        # --- kappa: mean coupling density normalised to [0, 1] ---
        if file_nodes:
            coupling_scores = [graph.coupling_score(nid) for nid in file_nodes]
            raw_mean_coupling = float(np.mean(coupling_scores))
            max_coupling = max(coupling_scores) if coupling_scores else 1.0
            normaliser = max(max_coupling, self.kappa_max)
            kappa = raw_mean_coupling / normaliser if normaliser > 0 else 0.0
        else:
            coupling_scores = []
            raw_mean_coupling = 0.0
            kappa = 0.0

        # --- per-component stress (normalized to 0-1) ---
        # Raw stress = coupling / cohesion can be arbitrarily large.
        # We normalize using: norm_stress = raw / (raw + k) where k is a
        # scaling constant.  This maps [0, inf) -> [0, 1) with k controlling
        # the midpoint (stress == k => norm 0.5).
        _STRESS_SCALE = 10.0  # raw stress of 10 maps to 0.5

        raw_stress_map: dict[str, float] = {}
        for nid in graph._graph.nodes:
            raw = graph.stress_score(nid)
            if not np.isfinite(raw):
                raw = 1e6  # cap infinite
            raw_stress_map[nid] = raw

        stress_map: dict[str, float] = {}
        for nid, raw in raw_stress_map.items():
            stress_map[nid] = raw / (raw + _STRESS_SCALE) if raw > 0 else 0.0

        sorted_stress = sorted(stress_map.items(), key=lambda x: x[1], reverse=True)
        top_stress = sorted_stress[:5]

        if sorted_stress:
            highest_component, highest_value = sorted_stress[0]
        else:
            highest_component, highest_value = "", 0.0

        # --- sigma: cascade margin = 1 - max(normalized stress) ---
        # If the most stressed component is at 0.9, sigma = 0.1 (close to collapse)
        max_norm_stress = highest_value if sorted_stress else 0.0
        sigma = 1.0 - max_norm_stress

        # --- equilibrium score ---
        es_score = self._compute_es(kappa, sigma)

        # --- phase ---
        phase = self._classify_phase(es_score)

        mean_coupling = graph.mean_coupling
        mean_cohesion = graph.mean_cohesion

        return SweepResult(
            kappa=kappa,
            sigma=sigma,
            es_score=es_score,
            phase=phase,
            highest_stress_component=highest_component,
            highest_stress_value=highest_value,
            total_nodes=total_nodes,
            total_edges=total_edges,
            mean_coupling=mean_coupling,
            mean_cohesion=mean_cohesion,
            top_stress_components=top_stress,
        )

    # ------------------------------------------------------------------
    # Change impact
    # ------------------------------------------------------------------

    def compute_change_impact(
        self, graph: CodebaseGraph, before_result: SweepResult
    ) -> dict:
        """Run a new sweep and return deltas against a previous result.

        Returns
        -------
        dict with keys:
            kappa_delta, sigma_delta, es_delta, phase_changed, moved_toward_ridge
        """
        after = self.run(graph)

        phase_order = {Phase.CRITICAL: 0, Phase.CAUTION: 1, Phase.RIDGE: 2}
        before_rank = phase_order[before_result.phase]
        after_rank = phase_order[after.phase]

        return {
            "kappa_delta": after.kappa - before_result.kappa,
            "sigma_delta": after.sigma - before_result.sigma,
            "es_delta": after.es_score - before_result.es_score,
            "phase_changed": after.phase != before_result.phase,
            "moved_toward_ridge": after_rank > before_rank,
            "before": before_result,
            "after": after,
        }

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def calibrate(self, graph: CodebaseGraph) -> None:
        """Auto-calibrate sigma_star and kappa_star from the current codebase state.

        Assumes the current state is near-optimal as a starting point:
        - kappa_star is set to the current kappa.
        - sigma_star is chosen so that the current sigma yields ES > 0.7.

        This is a heuristic — further tuning can be done by running sweeps on
        known-healthy and known-degraded snapshots.
        """
        result = self.run(graph)

        # Set kappa_star to the current coupling density
        self.kappa_star = result.kappa

        # Find sigma_star such that ES > 0.7 at the current sigma.
        # The Gaussian component is maximised when sigma == sigma_star,
        # so the simplest calibration is to centre there.
        self.sigma_star = result.sigma

        # Verify the calibration produces a healthy score.  If the kappa
        # penalty term drags ES below 0.7, widen kappa_max to compensate.
        es_check = self._compute_es(result.kappa, result.sigma)
        if es_check < 0.7 and self.kappa_max > 0:
            # Solve: alpha * 1.0 * (1 - |kappa - kappa_star| / kappa_max) >= 0.7
            # Since kappa == kappa_star after calibration, the kappa term is 1.0
            # and the issue is alpha < 0.7. Bump alpha.
            if self.alpha < 0.7:
                self.alpha = 1.0

        logger.info(
            "Calibrated NFET: kappa_star=%.4f, sigma_star=%.4f, alpha=%.2f",
            self.kappa_star,
            self.sigma_star,
            self.alpha,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_es(self, kappa: float, sigma: float) -> float:
        """Compute the equilibrium score.

        ES = alpha * exp(-beta * (sigma - sigma_star)^2)
                     * (1 - |kappa - kappa_star| / kappa_max)
        """
        gaussian = float(np.exp(-self.beta * (sigma - self.sigma_star) ** 2))
        kappa_penalty = 1.0 - abs(kappa - self.kappa_star) / self.kappa_max if self.kappa_max > 0 else 1.0
        # Clamp kappa_penalty to [0, 1] in case kappa drifts beyond kappa_max
        kappa_penalty = max(0.0, min(1.0, kappa_penalty))
        return self.alpha * gaussian * kappa_penalty

    @staticmethod
    def _classify_phase(es_score: float) -> Phase:
        """Map an equilibrium score to a structural phase."""
        if es_score > 0.7:
            return Phase.RIDGE
        if es_score > 0.4:
            return Phase.CAUTION
        return Phase.CRITICAL
