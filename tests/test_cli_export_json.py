from __future__ import annotations

import ast
import json
import math
from pathlib import Path

CLI_SOURCE = Path(__file__).resolve().parents[1] / "codey" / "cli.py"


def _load_json_safe():
    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    json_safe_def = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_json_safe"
    )
    module = ast.Module(body=[json_safe_def], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"math": math}
    exec(compile(module, str(CLI_SOURCE), "exec"), namespace)
    return namespace["_json_safe"]


def test_json_safe_replaces_non_finite_floats_for_strict_exports() -> None:
    payload = {
        "stress": float("inf"),
        "nodes": [
            {"stress": float("nan")},
            {"stress": 0.25},
        ],
        "tuple_metrics": (float("inf"), {"nested": (float("nan"),)}),
        "set_metrics": {float("inf")},
        "phase": "CRITICAL",
    }

    safe_payload = _load_json_safe()(payload)

    assert safe_payload["stress"] == 0.0
    assert safe_payload["nodes"][0]["stress"] == 0.0
    assert safe_payload["nodes"][1]["stress"] == 0.25
    assert safe_payload["tuple_metrics"] == [0.0, {"nested": [0.0]}]
    assert safe_payload["set_metrics"] == [0.0]
    assert "NaN" not in json.dumps(safe_payload, allow_nan=False)


def test_json_safe_serializes_non_json_edge_values_deterministically() -> None:
    class _Opaque:
        def __str__(self) -> str:
            return "opaque-value"

    cycle: dict[str, object] = {"name": "cycle"}
    cycle["self"] = cycle
    payload = {
        ("tuple", "key"): b"cli-bytes",
        "set_values": {"b", "a"},
        "opaque": _Opaque(),
        "cycle": cycle,
    }

    safe_payload = _load_json_safe()(payload)

    assert safe_payload == {
        "('tuple', 'key')": "cli-bytes",
        "set_values": ["a", "b"],
        "opaque": "opaque-value",
        "cycle": {
            "name": "cycle",
            "self": "[Circular]",
        },
    }
    json.dumps(safe_payload, allow_nan=False)


def test_cli_text_exports_write_utf8_explicitly() -> None:
    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    write_text_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write_text"
    ]

    assert write_text_calls
    assert all(
        any(
            keyword.arg == "encoding"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "utf-8"
            for keyword in node.keywords
        )
        for node in write_text_calls
    )
