"""Autonomous Monitor — watches the codebase for structural degradation and acts."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from codey.autonomous.audit_db import AuditDatabase
from codey.graph.engine import CodebaseGraph
from codey.nfet.health_db import HealthDatabase
from codey.nfet.sweep import NFETSweep, Phase, SweepResult
from codey.parser.extractor import LanguageParser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trigger conditions
# ---------------------------------------------------------------------------


class TriggerCondition(Enum):
    """Events that can trigger an autonomous response."""

    STRESS_THRESHOLD = "stress_threshold"
    TEST_FAILURE = "test_failure"
    LINT_ERROR = "lint_error"
    COVERAGE_DROP = "coverage_drop"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    PHASE_CHANGE = "phase_change"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AutonomousConfig:
    """Boundaries for autonomous behaviour.

    These defaults are intentionally conservative.  ``auto_refactor`` is
    *off* by default because structural refactoring can cascade in ways
    that are hard to predict without human review.
    """

    max_impact_radius: int = 15
    stress_threshold: float = 0.7
    min_coverage: float = 0.8
    auto_fix_lint: bool = True
    auto_fix_types: bool = True
    auto_refactor: bool = False  # dangerous — requires explicit opt-in
    phase_constraint: str = "RIDGE"  # never make changes that leave this phase
    sweep_interval: int = 60  # seconds between full NFET sweeps


# ---------------------------------------------------------------------------
# File-change handler (watchdog)
# ---------------------------------------------------------------------------


class _FileChangeHandler(FileSystemEventHandler):
    """Bridges watchdog file-system events to the monitor's callback."""

    def __init__(self, callback: callable) -> None:  # type: ignore[valid-type]
        super().__init__()
        self._callback = callback

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._callback(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._callback(event.src_path)


# ---------------------------------------------------------------------------
# Phase ordering helper
# ---------------------------------------------------------------------------

_PHASE_RANK: dict[Phase, int] = {
    Phase.CRITICAL: 0,
    Phase.CAUTION: 1,
    Phase.RIDGE: 2,
}


# ---------------------------------------------------------------------------
# Autonomous Monitor
# ---------------------------------------------------------------------------


class AutonomousMonitor:
    """Watches a codebase directory, runs periodic NFET sweeps, and responds
    to structural degradation autonomously — within configured safety bounds.

    The monitor has two input channels:

    1. **File watcher** (watchdog): fires on every save, re-parses the changed
       file, updates the graph, and checks trigger conditions.
    2. **Sweep loop** (background thread): runs a full NFET sweep on a
       configurable interval and watches for phase transitions.

    When a trigger fires, the monitor evaluates candidate actions, scores them
    by expected metric improvement, and — if the best candidate stays within
    the configured safety envelope — executes it with a full audit trail.
    If tests fail after execution, the change is rolled back.
    """

    def __init__(
        self,
        graph: CodebaseGraph,
        sweep_engine: NFETSweep,
        config: AutonomousConfig | None = None,
        audit_db: AuditDatabase | None = None,
        health_db: HealthDatabase | None = None,
    ) -> None:
        self.graph = graph
        self.sweep_engine = sweep_engine
        self.config = config or AutonomousConfig()
        self.audit_db = audit_db or AuditDatabase()
        self.health_db = health_db or HealthDatabase()

        self._parser = LanguageParser()
        self._running: bool = False
        self._watchers: list[Observer] = []
        self._last_sweep: SweepResult | None = None
        self._sweep_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending_triggers: list[tuple[TriggerCondition, str, dict]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, watch_path: Path) -> None:
        """Begin autonomous monitoring.

        Starts a watchdog observer on *watch_path* and a background thread
        that runs full NFET sweeps every ``config.sweep_interval`` seconds.
        """
        if self._running:
            logger.warning("Monitor already running — ignoring duplicate start()")
            return

        self._running = True
        watch_path = watch_path.resolve()

        # --- File watcher ---
        handler = _FileChangeHandler(self._on_file_change)
        observer = Observer()
        observer.schedule(handler, str(watch_path), recursive=True)
        observer.daemon = True
        observer.start()
        self._watchers.append(observer)

        # --- Sweep loop ---
        self._sweep_thread = threading.Thread(
            target=self._sweep_loop, name="codey-sweep-loop", daemon=True
        )
        self._sweep_thread.start()

        logger.info(
            "Autonomous monitor started — watching %s, sweep every %ds",
            watch_path,
            self.config.sweep_interval,
        )

    def stop(self) -> None:
        """Gracefully shut down the monitor."""
        if not self._running:
            return

        self._running = False

        # Stop file watchers
        for observer in self._watchers:
            observer.stop()
        for observer in self._watchers:
            observer.join(timeout=5.0)
        self._watchers.clear()

        # Wait for sweep thread
        if self._sweep_thread is not None:
            self._sweep_thread.join(timeout=self.config.sweep_interval + 5)
            self._sweep_thread = None

        logger.info("Autonomous monitor stopped.")

    # ------------------------------------------------------------------
    # File change callback
    # ------------------------------------------------------------------

    def _on_file_change(self, file_path: str) -> None:
        """Called by watchdog when a file is saved.

        Re-parses the file, updates the graph, checks triggers, and
        dispatches any that fire.
        """
        path = Path(file_path)

        # Only process files the parser can handle
        if path.suffix.lower() not in {".py", ".js", ".ts", ".jsx", ".tsx"}:
            return

        logger.debug("File change detected: %s", file_path)

        try:
            new_nodes, new_edges = self._parser.parse_file(path)
        except Exception as exc:
            logger.warning("Failed to re-parse %s: %s", file_path, exc)
            return

        with self._lock:
            self.graph.update_file(file_path, new_nodes, new_edges)

        # Check triggers for all nodes in this file
        triggers = self._check_triggers(file_path)
        for trigger, component, details in triggers:
            self._handle_trigger(trigger, component, details)

    # ------------------------------------------------------------------
    # Background sweep loop
    # ------------------------------------------------------------------

    def _sweep_loop(self) -> None:
        """Runs in a background thread.  Executes a full NFET sweep on the
        configured interval and watches for phase transitions."""
        logger.debug("Sweep loop started (interval=%ds)", self.config.sweep_interval)

        while self._running:
            try:
                self._run_single_sweep()
            except Exception as exc:
                logger.error("Sweep loop error: %s", exc, exc_info=True)

            # Sleep in small increments so we can exit promptly
            deadline = time.monotonic() + self.config.sweep_interval
            while self._running and time.monotonic() < deadline:
                time.sleep(1.0)

        logger.debug("Sweep loop exited.")

    def _run_single_sweep(self) -> None:
        """Execute one full NFET sweep, log it, and check for phase degradation."""
        with self._lock:
            result = self.sweep_engine.run(self.graph)

        # Persist to health database
        try:
            self.health_db.log_sweep(result)
        except Exception as exc:
            logger.warning("Failed to log sweep to health_db: %s", exc)

        # Check for phase degradation
        if self._last_sweep is not None:
            last_rank = _PHASE_RANK.get(self._last_sweep.phase, 2)
            current_rank = _PHASE_RANK.get(result.phase, 2)

            if current_rank < last_rank:
                logger.warning(
                    "Phase degradation detected: %s -> %s (ES: %.3f -> %.3f)",
                    self._last_sweep.phase.value,
                    result.phase.value,
                    self._last_sweep.es_score,
                    result.es_score,
                )
                self._handle_trigger(
                    TriggerCondition.PHASE_CHANGE,
                    result.highest_stress_component,
                    {
                        "previous_phase": self._last_sweep.phase.value,
                        "current_phase": result.phase.value,
                        "es_before": self._last_sweep.es_score,
                        "es_after": result.es_score,
                        "top_stress": result.top_stress_components,
                    },
                )

        self._last_sweep = result
        logger.debug(
            "Sweep complete: phase=%s ES=%.3f kappa=%.3f sigma=%.3f",
            result.phase.value,
            result.es_score,
            result.kappa,
            result.sigma,
        )

    # ------------------------------------------------------------------
    # Trigger checking
    # ------------------------------------------------------------------

    def _check_triggers(
        self, file_path: str
    ) -> list[tuple[TriggerCondition, str, dict]]:
        """Check all trigger conditions for nodes in a file after an update.

        Returns a list of (trigger, component_id, details) tuples for every
        condition that fires.
        """
        triggers: list[tuple[TriggerCondition, str, dict]] = []
        module_nodes = self.graph.get_module_nodes(file_path)

        for node_id in module_nodes:
            stress = self.graph.stress_score(node_id)

            # --- STRESS_THRESHOLD ---
            if stress > self.config.stress_threshold:
                triggers.append((
                    TriggerCondition.STRESS_THRESHOLD,
                    node_id,
                    {
                        "stress": stress,
                        "threshold": self.config.stress_threshold,
                        "file_path": file_path,
                    },
                ))

        # --- CIRCULAR_DEPENDENCY ---
        # Check if any node in this file participates in a cycle
        for node_id in module_nodes:
            try:
                # Check for self-referential or short cycles through this node
                impact = self.graph.impact_radius(node_id)
                if node_id in impact:
                    # impact_radius excludes the start node, so if it shows up
                    # something is wrong — but more practically, check if any
                    # of this node's dependents point back to it
                    pass
                # Use cascade_depth as a heuristic: if a node can reach itself
                # through the graph, we have a cycle.  NetworkX gives us a
                # direct way to check.
                import networkx as nx

                if node_id in self.graph._graph:
                    # Check for cycles involving this node
                    try:
                        cycle = nx.find_cycle(self.graph._graph, source=node_id)
                        if cycle:
                            cycle_nodes = [e[0] for e in cycle]
                            triggers.append((
                                TriggerCondition.CIRCULAR_DEPENDENCY,
                                node_id,
                                {
                                    "cycle": cycle_nodes,
                                    "file_path": file_path,
                                },
                            ))
                    except nx.NetworkXNoCycle:
                        pass
            except Exception as exc:
                logger.debug("Cycle check failed for %s: %s", node_id, exc)

        return triggers

    # ------------------------------------------------------------------
    # Trigger handling — the autonomous decision algorithm
    # ------------------------------------------------------------------

    def _handle_trigger(
        self,
        trigger: TriggerCondition,
        component: str,
        details: dict,
    ) -> None:
        """The autonomous decision algorithm.

        1. Log that the trigger was detected.
        2. Check if action is within configured boundaries.
        3. If auto-action is enabled for this trigger type, generate candidates.
        4. Score candidates by expected metric improvement.
        5. Select the best candidate that stays within constraints.
        6. Execute with full audit trail.
        7. If tests fail after execution, rollback.

        The actual code modification step is a placeholder — real modifications
        will be routed through CodeAgent once that integration is complete.
        """
        logger.info(
            "Trigger fired: %s on component %s — %s",
            trigger.value,
            component,
            details,
        )

        with self._lock:
            self._pending_triggers.append((trigger, component, details))

        # --- Boundary check ---
        if not self._is_within_boundaries(component):
            logger.info(
                "Action suppressed for %s: exceeds max_impact_radius (%d) "
                "or would violate phase_constraint (%s)",
                component,
                self.config.max_impact_radius,
                self.config.phase_constraint,
            )
            return

        # --- Check if auto-action is enabled for this trigger ---
        if not self._is_auto_enabled(trigger):
            logger.info(
                "Auto-action not enabled for trigger %s — logging only.",
                trigger.value,
            )
            return

        # --- Capture before-state ---
        before_sweep = self._last_sweep
        if before_sweep is None:
            # Run a sweep to establish baseline
            with self._lock:
                before_sweep = self.sweep_engine.run(self.graph)
            self._last_sweep = before_sweep

        stress_before = self.graph.stress_score(component) if component in self.graph._graph else 0.0

        # --- Generate and score candidates ---
        candidates = self._generate_candidates(trigger, component, details)

        if not candidates:
            logger.info("No viable candidates generated for %s on %s", trigger.value, component)
            return

        # Score each candidate: higher is better
        scored = []
        for candidate in candidates:
            score = self._score_candidate(candidate, before_sweep)
            scored.append((score, candidate))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_candidate = scored[0]

        if best_score <= 0:
            logger.info(
                "Best candidate score is %.3f (non-positive) — skipping action.",
                best_score,
            )
            return

        # --- Execute the candidate ---
        logger.info(
            "Executing autonomous action: %s (score=%.3f)",
            best_candidate.get("description", "unknown"),
            best_score,
        )

        # PLACEHOLDER: actual code modification goes through CodeAgent.
        # For now, we log the intent and record it in the audit trail.
        change_diff = (
            f"[PLACEHOLDER] Would apply: {best_candidate.get('description', 'unknown')}\n"
            f"Target: {component}\n"
            f"Trigger: {trigger.value}\n"
            f"Candidate details: {best_candidate}"
        )
        test_result = "skipped — placeholder execution"
        rolled_back = False

        # --- Capture after-state (unchanged since placeholder) ---
        stress_after = stress_before  # no actual change yet

        with self._lock:
            after_sweep = self.sweep_engine.run(self.graph)

        # --- Audit trail ---
        self.audit_db.log_action(
            trigger_condition=trigger.value,
            component_affected=component,
            stress_before=stress_before,
            stress_after=stress_after,
            kappa_before=before_sweep.kappa,
            kappa_after=after_sweep.kappa,
            sigma_before=before_sweep.sigma,
            sigma_after=after_sweep.sigma,
            es_before=before_sweep.es_score,
            es_after=after_sweep.es_score,
            change_diff=change_diff,
            test_result=test_result,
            rolled_back=rolled_back,
        )

        # Remove from pending
        with self._lock:
            try:
                self._pending_triggers.remove((trigger, component, details))
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Decision support methods
    # ------------------------------------------------------------------

    def _is_within_boundaries(self, component: str) -> bool:
        """Check if acting on this component stays within the configured safety envelope."""
        # Impact radius check
        if component in self.graph._graph:
            radius = self.graph.impact_radius(component)
            if len(radius) > self.config.max_impact_radius:
                logger.debug(
                    "Impact radius for %s is %d (max=%d) — out of bounds",
                    component,
                    len(radius),
                    self.config.max_impact_radius,
                )
                return False

        # Phase constraint check: if we are currently in the constrained phase
        # (or better), don't risk leaving it
        if self._last_sweep is not None:
            constraint_phase = Phase(self.config.phase_constraint.lower())
            constraint_rank = _PHASE_RANK.get(constraint_phase, 2)
            current_rank = _PHASE_RANK.get(self._last_sweep.phase, 0)
            # Only allow action if we are at or above the constraint phase,
            # OR if we are below it (in which case action is needed to recover)
            if current_rank >= constraint_rank:
                # We're in a good state — only allow if action won't degrade
                pass  # let the execution + rollback logic handle degradation
            # If we're below the constraint phase, action is allowed (we need to recover)

        return True

    def _is_auto_enabled(self, trigger: TriggerCondition) -> bool:
        """Check whether the config permits autonomous action for this trigger type."""
        mapping: dict[TriggerCondition, bool] = {
            TriggerCondition.LINT_ERROR: self.config.auto_fix_lint,
            TriggerCondition.TEST_FAILURE: False,  # never auto-fix test failures
            TriggerCondition.STRESS_THRESHOLD: self.config.auto_refactor,
            TriggerCondition.COVERAGE_DROP: False,  # informational only
            TriggerCondition.CIRCULAR_DEPENDENCY: self.config.auto_refactor,
            TriggerCondition.PHASE_CHANGE: self.config.auto_refactor,
        }
        return mapping.get(trigger, False)

    def _generate_candidates(
        self,
        trigger: TriggerCondition,
        component: str,
        details: dict,
    ) -> list[dict[str, Any]]:
        """Generate candidate actions for a trigger.

        Each candidate is a dict with at minimum:
        - description: human-readable summary
        - action_type: what kind of change this represents
        - estimated_es_delta: rough estimate of ES improvement
        - estimated_coverage_delta: rough estimate of coverage impact
        - estimated_complexity_delta: rough estimate of complexity reduction

        These are heuristic estimates used for ranking — the real impact is
        measured after execution.
        """
        candidates: list[dict[str, Any]] = []

        if trigger == TriggerCondition.LINT_ERROR:
            candidates.append({
                "description": f"Auto-fix lint errors in {component}",
                "action_type": "lint_fix",
                "estimated_es_delta": 0.01,
                "estimated_coverage_delta": 0.0,
                "estimated_complexity_delta": -0.5,
                "details": details,
            })

        elif trigger == TriggerCondition.STRESS_THRESHOLD:
            stress = details.get("stress", 0.0)
            # Suggest extracting high-coupling dependencies
            candidates.append({
                "description": f"Extract dependencies to reduce stress on {component} (stress={stress:.2f})",
                "action_type": "extract_dependency",
                "estimated_es_delta": 0.05,
                "estimated_coverage_delta": 0.0,
                "estimated_complexity_delta": -2.0,
                "details": details,
            })
            # Suggest interface introduction
            candidates.append({
                "description": f"Introduce interface boundary to decouple {component}",
                "action_type": "introduce_interface",
                "estimated_es_delta": 0.08,
                "estimated_coverage_delta": -0.02,
                "estimated_complexity_delta": 1.0,
                "details": details,
            })

        elif trigger == TriggerCondition.CIRCULAR_DEPENDENCY:
            cycle = details.get("cycle", [])
            candidates.append({
                "description": f"Break circular dependency involving {component} ({len(cycle)} nodes)",
                "action_type": "break_cycle",
                "estimated_es_delta": 0.1,
                "estimated_coverage_delta": 0.0,
                "estimated_complexity_delta": -1.0,
                "details": details,
            })

        elif trigger == TriggerCondition.PHASE_CHANGE:
            candidates.append({
                "description": (
                    f"Emergency stabilisation: phase degraded from "
                    f"{details.get('previous_phase', '?')} to {details.get('current_phase', '?')}"
                ),
                "action_type": "phase_stabilise",
                "estimated_es_delta": 0.15,
                "estimated_coverage_delta": 0.0,
                "estimated_complexity_delta": 0.0,
                "details": details,
            })

        return candidates

    def _score_candidate(
        self, candidate: dict[str, Any], before_sweep: SweepResult
    ) -> float:
        """Score a candidate action by weighted sum of expected metric deltas.

        Score = es_delta_weight * es_delta
              + coverage_weight * coverage_delta
              + complexity_weight * complexity_delta

        Higher is better.  Complexity delta is negative-is-good, so it
        contributes positively when complexity decreases.
        """
        es_delta = candidate.get("estimated_es_delta", 0.0)
        coverage_delta = candidate.get("estimated_coverage_delta", 0.0)
        complexity_delta = candidate.get("estimated_complexity_delta", 0.0)

        # Weights — ES improvement is the primary objective
        w_es = 10.0
        w_coverage = 5.0
        w_complexity = 1.0

        score = (
            w_es * es_delta
            + w_coverage * coverage_delta
            + w_complexity * (-complexity_delta)  # negative complexity is good
        )
        return score

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current monitor status for dashboards and CLI queries."""
        with self._lock:
            pending = list(self._pending_triggers)

        recent_actions = self.audit_db.get_recent(limit=5)

        last_sweep_info: dict | None = None
        if self._last_sweep is not None:
            last_sweep_info = {
                "phase": self._last_sweep.phase.value,
                "es_score": self._last_sweep.es_score,
                "kappa": self._last_sweep.kappa,
                "sigma": self._last_sweep.sigma,
                "highest_stress_component": self._last_sweep.highest_stress_component,
                "highest_stress_value": self._last_sweep.highest_stress_value,
                "timestamp": self._last_sweep.timestamp,
            }

        return {
            "running": self._running,
            "last_sweep": last_sweep_info,
            "pending_triggers": [
                {
                    "trigger": t.value,
                    "component": c,
                    "details": d,
                }
                for t, c, d in pending
            ],
            "recent_actions_count": len(recent_actions),
            "total_rollbacks": self.audit_db.get_rollback_count(),
        }
