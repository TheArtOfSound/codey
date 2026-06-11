from __future__ import annotations

import uuid
from types import SimpleNamespace

from codey.saas.security.audit import (
    AuditLogger,
    _coerce_audit_count,
    _coerce_audit_float,
    _coerce_audit_row_list,
    _get_audit_row_value,
)


def test_row_to_dict_tolerates_string_created_at() -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        action="login_success",
        resource_type=None,
        resource_id=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        result="success",
        failure_reason=None,
        metadata_={"source": "test"},
        created_at=" 2026-01-02T03:04:05Z ",
    )

    payload = AuditLogger._row_to_dict(row)

    assert payload["created_at"] == "2026-01-02T03:04:05Z"
    assert payload["metadata"] == {"source": "test"}


def test_audit_float_coercion_rejects_non_finite_values() -> None:
    assert _coerce_audit_float(float("nan"), 7.0) == 7.0
    assert _coerce_audit_float("inf", 7.0) == 7.0
    assert _coerce_audit_float(10**10000, 7.0) == 7.0
    assert _coerce_audit_float("2.5", 7.0) == 2.5


def test_audit_count_coercion_rejects_malformed_values() -> None:
    assert _coerce_audit_count(None) == 0
    assert _coerce_audit_count(True) == 0
    assert _coerce_audit_count(-1) == 0
    assert _coerce_audit_count(float("nan")) == 0
    assert _coerce_audit_count(float("inf")) == 0
    assert _coerce_audit_count("5") == 5


def test_audit_row_value_skips_malformed_rows() -> None:
    assert _get_audit_row_value(("day", 4, 2), 1) == 4
    assert _get_audit_row_value(("day",), 2) is None
    assert _get_audit_row_value(None, 0) is None


def test_audit_row_list_coercion_rejects_malformed_results() -> None:
    row = SimpleNamespace(id="row-1")

    assert _coerce_audit_row_list([row]) == [row]
    assert _coerce_audit_row_list((row,)) == [row]
    assert _coerce_audit_row_list(None) == []
    assert _coerce_audit_row_list("bad") == []
