from __future__ import annotations

import json

from codey.nfet.state import ActionCandidate, ActionSimulation, NodeState, RepoState


def test_nfet_state_serialization_replaces_non_finite_floats() -> None:
    huge_int = 10**10000
    node = NodeState(
        node_id="node-1",
        name="app.py",
        file_path="app.py",
        kind="file",
        stress=float("nan"),
        coupling=float("inf"),
        cohesion=float("-inf"),
        complexity=3.0,
        cascade_depth=1,
        impact_radius=2,
        fanin=0,
        fanout=1,
        betweenness=float("nan"),
        shared_state_edges=0,
        cycle_detected=False,
        sigma=huge_int,
        kappa=0.2,
        gamma=float("nan"),
        es=0.8,
        risk_score=float("inf"),
        risk_level="high",
    )
    candidate = ActionCandidate(
        candidate_id="cand-1",
        kind="extract",
        title="Extract logic",
        description="Extract logic",
        target_node_id="node-1",
        target_file_path="app.py",
        predicted_repo_es_delta=float("nan"),
        predicted_sigma_reduction=0.1,
        predicted_kappa_reduction=float("inf"),
        risk=float("-inf"),
        cost=0.2,
        reversibility=float("nan"),
        confidence=0.7,
        score=float("inf"),
    )
    state = RepoState(
        phase="critical",
        global_kappa=float("nan"),
        global_sigma=0.3,
        global_es=float("inf"),
        total_nodes=1,
        total_edges=0,
        highest_stress_component="node-1",
        highest_stress_value=float("nan"),
        components=[node],
        hotspots=[node],
    )
    simulation = ActionSimulation(
        candidate=candidate,
        before_component_es=float("nan"),
        after_component_es=0.4,
        before_repo_es=float("inf"),
        after_repo_es=0.5,
        predicted_phase="caution",
        narrative="Expected improvement",
    )

    payload = {"state": state.to_dict(), "simulation": simulation.to_dict()}

    assert payload["state"]["global_kappa"] == 0.0
    assert payload["state"]["components"][0]["stress"] == 0.0
    assert payload["simulation"]["candidate"]["score"] == 0.0
    assert "NaN" not in json.dumps(payload, allow_nan=False)
