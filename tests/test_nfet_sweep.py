from __future__ import annotations

from codey.nfet.sweep import NFETSweep, Phase


class _FakeNodeView:
    def __init__(self) -> None:
        self._items = [("file_a", {"kind": "file"})]

    def __call__(self, data: bool = False):
        if data:
            return self._items
        return [node_id for node_id, _data in self._items]

    def __iter__(self):
        return iter([node_id for node_id, _data in self._items])


class _FakeInnerGraph:
    nodes = _FakeNodeView()


class _MalformedSummaryGraph:
    _graph = _FakeInnerGraph()
    node_count = 1
    edge_count = 0
    mean_coupling = "bad"
    mean_cohesion = float("inf")

    def coupling_score(self, _node_id: str):
        return "bad"

    def stress_score(self, _node_id: str):
        return "bad"


def test_compute_es_rejects_non_finite_inputs() -> None:
    sweep = NFETSweep()

    assert sweep._compute_es(float("nan"), 0.5) == 0.0
    assert sweep._compute_es(0.4, float("inf")) == 0.0
    assert sweep._compute_es(10**10000, 0.5) == 0.0

    sweep.alpha = float("inf")
    assert sweep._compute_es(0.4, 0.5) == 0.0


def test_normalize_stress_handles_malformed_values() -> None:
    assert NFETSweep._normalize_stress(None) > 0.999
    assert NFETSweep._normalize_stress("bad") > 0.999
    assert NFETSweep._normalize_stress(float("inf")) > 0.999
    assert NFETSweep._normalize_stress("-1") == 0.0
    assert NFETSweep._normalize_stress(10.0, scale=0) == 0.5


def test_coerce_nonnegative_metric_handles_malformed_values() -> None:
    assert NFETSweep._coerce_nonnegative_metric(None, default=0.4) == 0.4
    assert NFETSweep._coerce_nonnegative_metric("bad", default=0.4) == 0.4
    assert NFETSweep._coerce_nonnegative_metric(float("inf")) == 1e6
    assert NFETSweep._coerce_nonnegative_metric(10**10000) == 1e6
    assert NFETSweep._coerce_nonnegative_metric("-1", default=0.4) == 0.4
    assert NFETSweep._coerce_nonnegative_metric("1.5") == 1.5


def test_run_normalizes_malformed_summary_metrics() -> None:
    result = NFETSweep().run(_MalformedSummaryGraph())  # type: ignore[arg-type]

    assert result.kappa == 0.0
    assert result.mean_coupling == 0.0
    assert result.mean_cohesion == 1.0
    assert result.highest_stress_value > 0.999


def test_classify_phase_treats_non_finite_score_as_critical() -> None:
    assert NFETSweep._classify_phase(float("nan")) is Phase.CRITICAL
