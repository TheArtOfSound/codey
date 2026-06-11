from __future__ import annotations

from codey.autonomous.audit_db import (
    AuditDatabase,
    _coerce_audit_float,
    _coerce_recent_limit,
)


def _log_action(db: AuditDatabase, component: str) -> None:
    db.log_action(
        trigger_condition="phase_transition",
        component_affected=component,
        stress_before=0.5,
        stress_after=0.4,
        kappa_before=0.3,
        kappa_after=0.2,
        sigma_before=0.6,
        sigma_after=0.7,
        es_before=0.5,
        es_after=0.6,
        change_diff="diff",
        test_result="passed",
    )


def test_coerce_recent_limit_rejects_unbounded_values() -> None:
    assert _coerce_recent_limit(True) == 20
    assert _coerce_recent_limit(0) == 20
    assert _coerce_recent_limit(-1) == 20
    assert _coerce_recent_limit(float("inf")) == 20
    assert _coerce_recent_limit(10**10000) == 1000
    assert _coerce_recent_limit("3") == 3


def test_coerce_audit_float_rejects_non_finite_values() -> None:
    assert _coerce_audit_float(True) == 0.0
    assert _coerce_audit_float(float("nan")) == 0.0
    assert _coerce_audit_float(float("inf")) == 0.0
    assert _coerce_audit_float("-inf") == 0.0
    assert _coerce_audit_float(10**10000) == 0.0
    assert _coerce_audit_float("0.25") == 0.25


def test_get_recent_does_not_treat_negative_limit_as_unbounded() -> None:
    db = AuditDatabase(":memory:")
    try:
        for index in range(25):
            _log_action(db, f"component-{index}")

        rows = db.get_recent(limit=-1)
    finally:
        db.close()

    assert len(rows) == 20
    assert rows[0]["component_affected"] == "component-24"


def test_log_action_sanitizes_non_finite_metrics() -> None:
    db = AuditDatabase(":memory:")
    try:
        db.log_action(
            trigger_condition="phase_transition",
            component_affected="component",
            stress_before=float("nan"),
            stress_after=float("inf"),
            kappa_before="-inf",
            kappa_after="0.2",
            sigma_before=True,
            sigma_after=None,  # type: ignore[arg-type]
            es_before=10**10000,
            es_after="0.6",
            change_diff="diff",
            test_result="passed",
        )
        row = db.get_recent(limit=1)[0]
    finally:
        db.close()

    assert row["stress_before"] == 0.0
    assert row["stress_after"] == 0.0
    assert row["kappa_before"] == 0.0
    assert row["kappa_after"] == 0.2
    assert row["sigma_before"] == 0.0
    assert row["sigma_after"] == 0.0
    assert row["es_before"] == 0.0
    assert row["es_after"] == 0.6
