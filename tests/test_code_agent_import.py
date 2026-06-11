from __future__ import annotations

import builtins
import importlib
import sys


def test_code_agent_import_defers_graph_and_numeric_dependencies(monkeypatch) -> None:
    parent = sys.modules.get("codey.llm")
    for module_name in (
        "codey.llm.code_agent",
        "codey.llm.prompt_builder",
        "codey.graph.engine",
        "codey.nfet.sweep",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    if parent is not None:
        monkeypatch.delattr(parent, "code_agent", raising=False)
        monkeypatch.delattr(parent, "prompt_builder", raising=False)
    for module_name in list(sys.modules):
        if module_name == "networkx" or module_name.startswith("networkx."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
        if module_name == "numpy" or module_name.startswith("numpy."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "networkx" or name.startswith("networkx."):
            raise ModuleNotFoundError("No module named 'networkx'", name="networkx")
        if name == "numpy" or name.startswith("numpy."):
            raise ModuleNotFoundError("No module named 'numpy'", name="numpy")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.llm.code_agent")

    assert module.CodeAgent._parse_json_response('{"ok": true}', None) == {"ok": True}
    assert module.CodeAgent._parse_json_response({"ok": True}, None) == {"ok": True}
    assert module.CodeAgent._parse_json_response([{"ok": True}], None) == [{"ok": True}]
    assert module.CodeAgent._parse_json_response(None, {"fallback": True}) == {
        "fallback": True,
    }

    agent = module.CodeAgent.__new__(module.CodeAgent)
    bytes_response = agent._parse_generation_response(b"print('ok')", None, None)
    assert bytes_response["code"] == "print('ok')"

    list_response = agent._parse_generation_response(["code"], None, None)
    assert list_response["code"] == '["code"]'
    assert list_response["explanation"] == "Response was not in structured JSON format."

    structured_response = agent._parse_generation_response({"code": b"print(1)"}, None, None)
    assert structured_response["code"] == "print(1)"
    assert "structural_impact" in structured_response

    refactor_response = agent._parse_refactor_response(["code"])
    assert refactor_response["suggestions"] == ['["code"]']
    assert refactor_response["estimated_improvement"] == {
        "stress_delta": 0.0,
        "coupling_delta": 0.0,
        "cohesion_delta": 0.0,
    }

    structured_refactor = agent._parse_refactor_response({
        "suggestions": [b"move helper"],
        "estimated_improvement": {"stress_delta": -1.0},
    })
    assert structured_refactor["suggestions"] == ["move helper"]
    assert structured_refactor["estimated_improvement"] == {
        "stress_delta": -1.0,
        "coupling_delta": 0.0,
        "cohesion_delta": 0.0,
    }

    impact_response = agent._parse_impact_response(["impact"])
    assert impact_response["impact_summary"] == '["impact"]'
    assert impact_response["risk_level"] == "moderate"
    assert impact_response["affected_components"] == []

    structured_impact = agent._parse_impact_response({
        "impact_summary": b"risk",
        "risk_level": 3,
        "affected_components": [b"a.py"],
    })
    assert structured_impact["impact_summary"] == "risk"
    assert structured_impact["risk_level"] == "3"
    assert structured_impact["affected_components"] == ["a.py"]
    assert structured_impact["recommendation"] == (
        "Unable to parse structured response. Review the raw analysis above."
    )

    assert "codey.llm.prompt_builder" not in sys.modules
