from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import codey.dashboard.server as dashboard_server
from codey.dashboard.server import (
    DashboardState,
    _dashboard_json_safe,
    _format_change_record,
    _format_history_record,
    _normalize_dashboard_stress,
    _safe_bool,
    _safe_float,
    _safe_round,
    _safe_top_stress_components,
)


class _MutatingWebSocket:
    def __init__(self, state: DashboardState, to_remove=None) -> None:
        self.state = state
        self.to_remove = to_remove
        self.payloads: list[str] = []

    async def send_text(self, payload: str) -> None:
        if self.to_remove is not None:
            self.state.connected_clients.discard(self.to_remove)
        self.payloads.append(payload)


@pytest.mark.asyncio
async def test_dashboard_broadcast_handles_client_set_mutation() -> None:
    state = DashboardState()
    second = _MutatingWebSocket(state)
    first = _MutatingWebSocket(state, to_remove=second)
    state.connected_clients = {first, second}

    await state.broadcast({"status": "ok"})

    assert first.payloads == ['{"status": "ok"}']
    assert second.payloads == ['{"status": "ok"}']
    assert state.connected_clients == {first}


@pytest.mark.asyncio
async def test_dashboard_broadcast_sanitizes_non_finite_json() -> None:
    state = DashboardState()
    client = _MutatingWebSocket(state)
    state.connected_clients = {client}

    await state.broadcast({
        "stress": float("inf"),
        "nodes": [{"stress": float("nan")}],
    })

    assert json.loads(client.payloads[0]) == {
        "stress": 0.0,
        "nodes": [{"stress": 0.0}],
    }


def test_safe_round_handles_malformed_and_non_finite_values() -> None:
    assert _safe_round("bad") == 0.0
    assert _safe_round(True) == 0.0
    assert _safe_round(float("inf")) == 0.0
    assert _safe_round(float("nan")) == 0.0
    assert _safe_round(0.123456) == 0.1235


def test_safe_float_handles_malformed_and_non_finite_values() -> None:
    assert _safe_float("bad") == 0.0
    assert _safe_float(True) == 0.0
    assert _safe_float(float("inf")) == 0.0
    assert _safe_float(float("nan")) == 0.0
    assert _safe_float("0.123456") == 0.123456


def test_safe_bool_handles_legacy_database_values() -> None:
    assert _safe_bool(True) is True
    assert _safe_bool(False) is False
    assert _safe_bool(1) is True
    assert _safe_bool(0) is False
    assert _safe_bool(float("nan")) is False
    assert _safe_bool("1") is True
    assert _safe_bool("0") is False
    assert _safe_bool("false") is False
    assert _safe_bool(b"true") is True
    assert _safe_bool("unexpected") is False


def test_dashboard_history_record_formatting_sanitizes_legacy_rows() -> None:
    payload = _format_history_record(
        {
            "timestamp": b"2026-01-01T00:00:00Z",
            "es_score": float("inf"),
            "kappa": "bad",
            "sigma": "0.123456",
            "phase": None,
        }
    )

    assert payload == {
        "timestamp": "2026-01-01T00:00:00Z",
        "es_score": 0.0,
        "kappa": 0.0,
        "sigma": 0.1235,
        "phase": "",
    }
    json.dumps(payload, allow_nan=False)


def test_dashboard_change_record_formatting_sanitizes_legacy_rows() -> None:
    payload = _format_change_record(
        {
            "timestamp": b"2026-01-01T00:00:00Z",
            "trigger_condition": None,
            "component_affected": 123,
            "stress_before": float("inf"),
            "stress_after": "0.25",
            "es_before": "bad",
            "es_after": "0.6",
            "rolled_back": "0",
        }
    )

    assert payload == {
        "timestamp": "2026-01-01T00:00:00Z",
        "trigger": "",
        "component": "123",
        "stress_before": 0.0,
        "stress_after": 0.25,
        "es_before": 0.0,
        "es_after": 0.6,
        "rolled_back": False,
    }
    json.dumps(payload, allow_nan=False)


def test_safe_top_stress_components_skips_malformed_entries() -> None:
    components = _safe_top_stress_components(
        [
            ("core.py", "0.75"),
            {"id": "api.py", "stress": float("inf")},
            ("", 0.9),
            ["bad-node"],
            "malformed",
            (["unhashable"], 0.8),
            ("ignored.py", 0.9),
        ],
        limit=2,
    )

    assert components == [("core.py", 0.75), ("api.py", 0.0)]
    assert _safe_top_stress_components(None) == []


def test_normalize_dashboard_stress_handles_non_finite_values() -> None:
    assert _normalize_dashboard_stress(float("inf")) == 1.0
    assert _normalize_dashboard_stress(float("nan")) == 0.0
    assert _normalize_dashboard_stress("bad") == 0.0
    assert _normalize_dashboard_stress(-1) == 0.0
    assert _normalize_dashboard_stress(10.0, scale=10.0) == 0.5
    assert _normalize_dashboard_stress(10.0, scale=float("nan")) == 0.5


def test_dashboard_json_safe_sanitizes_nested_containers() -> None:
    payload = _dashboard_json_safe({
        "tuple_metrics": (float("inf"), {"nested": (float("nan"),)}),
        "set_metrics": {float("inf")},
    })

    assert payload == {
        "tuple_metrics": [0.0, {"nested": [0.0]}],
        "set_metrics": [0.0],
    }
    json.dumps(payload, allow_nan=False)


def test_dashboard_json_safe_serializes_non_json_edge_values() -> None:
    class _Opaque:
        def __str__(self) -> str:
            return "opaque-value"

    cycle: dict[str, object] = {"name": "cycle"}
    cycle["self"] = cycle
    payload = _dashboard_json_safe(
        {
            ("tuple", "key"): b"dashboard-bytes",
            "set_values": {"b", "a"},
            "opaque": _Opaque(),
            "cycle": cycle,
        }
    )

    assert payload == {
        "('tuple', 'key')": "dashboard-bytes",
        "set_values": ["a", "b"],
        "opaque": "opaque-value",
        "cycle": {
            "name": "cycle",
            "self": "[Circular]",
        },
    }
    json.dumps(payload, allow_nan=False)


def test_dashboard_source_reads_text_with_explicit_utf8() -> None:
    tree = ast.parse(Path(dashboard_server.__file__).read_text(encoding="utf-8"))
    read_text_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_text"
    ]

    assert read_text_calls
    assert all(
        any(
            keyword.arg == "encoding"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "utf-8"
            for keyword in node.keywords
        )
        for node in read_text_calls
    )
