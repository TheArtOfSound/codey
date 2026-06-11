from __future__ import annotations

import math
from types import SimpleNamespace

from codey.nfet.health_db import (
    HealthDatabase,
    _coerce_history_float,
    _coerce_history_hours,
    _coerce_history_int,
    _coerce_history_row_list,
    _coerce_history_text,
    _history_row_to_dict,
    _linear_regression_slope,
)


def test_history_row_list_coercion_rejects_malformed_results() -> None:
    row = {"id": 1}

    assert _coerce_history_row_list([row]) == [row]
    assert _coerce_history_row_list((row,)) == [row]
    assert _coerce_history_row_list(None) == []
    assert _coerce_history_row_list("bad") == []


def test_history_row_to_dict_skips_malformed_rows() -> None:
    assert _history_row_to_dict({"id": 1}) == {"id": 1}
    assert _history_row_to_dict([("id", 1)]) == {"id": 1}
    assert _history_row_to_dict("bad") is None


def test_history_value_coercion_rejects_malformed_values() -> None:
    assert _coerce_history_float(float("nan")) == 0.0
    assert _coerce_history_float(float("inf")) == 0.0
    assert _coerce_history_float("0.42") == 0.42
    assert _coerce_history_int(True) == 0
    assert _coerce_history_int(-1) == 0
    assert _coerce_history_int(10**10000) == 1_000_000_000
    assert _coerce_history_hours(True) == 24
    assert _coerce_history_hours(0) == 24
    assert _coerce_history_hours(-1) == 24
    assert _coerce_history_hours("2") == 2
    assert _coerce_history_hours("bad") == 24
    assert _coerce_history_hours(10**10000) == 87_600
    assert _coerce_history_text("  phase  ") == "phase"
    assert _coerce_history_text(None, default="unknown") == "unknown"


def test_linear_regression_slope_uses_local_fallback() -> None:
    assert math.isclose(_linear_regression_slope([0, 1, 2], [1, 2, 3]), 1.0)
    assert math.isclose(_linear_regression_slope([0, 1, 2], [3, 2, 1]), -1.0)
    assert _linear_regression_slope([0, 1], ["bad", 1]) == 0.0
    assert _linear_regression_slope([0, 1], [math.nan, 1]) == 0.0
    assert _linear_regression_slope([0, 1], [10**10000, 1]) == 0.0


def test_log_sweep_sanitizes_malformed_snapshot_values(tmp_path) -> None:
    db = HealthDatabase(str(tmp_path / "health.db"))
    try:
        db.log_sweep(
            SimpleNamespace(
                timestamp=" 2026-01-01T00:00:00+00:00 ",
                kappa=float("nan"),
                sigma=float("inf"),
                es_score="-inf",
                phase="critical",
                highest_stress_component=123,
                highest_stress_value=float("inf"),
                total_nodes="bad",
                total_edges=10**10000,
                mean_coupling="0.5",
                mean_cohesion=None,
            )
        )

        latest = db.get_latest()
    finally:
        db.close()

    assert latest is not None
    assert latest["timestamp"] == "2026-01-01T00:00:00+00:00"
    assert latest["kappa"] == 0.0
    assert latest["sigma"] == 0.0
    assert latest["es_score"] == 0.0
    assert latest["phase"] == "critical"
    assert latest["highest_stress_component"] == "123"
    assert latest["highest_stress_value"] == 0.0
    assert latest["total_nodes"] == 0
    assert latest["total_edges"] == 1_000_000_000
    assert latest["mean_coupling"] == 0.5
    assert latest["mean_cohesion"] == 0.0


def test_history_reads_coerce_malformed_hours(tmp_path) -> None:
    db = HealthDatabase(str(tmp_path / "health.db"))
    try:
        assert db.get_history("bad") == []  # type: ignore[arg-type]
        assert db.get_trend(0) == {
            "es_direction": "stable",
            "kappa_direction": "stable",
            "sigma_direction": "stable",
        }
    finally:
        db.close()
