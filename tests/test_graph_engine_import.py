from __future__ import annotations

import builtins
import importlib
import sys

import pytest


def test_graph_engine_imports_without_networkx_until_graph_construction(
    monkeypatch,
) -> None:
    parent = sys.modules.get("codey.graph")
    monkeypatch.delitem(sys.modules, "codey.graph.engine", raising=False)
    if parent is not None:
        monkeypatch.delattr(parent, "engine", raising=False)
    for module_name in list(sys.modules):
        if module_name == "networkx" or module_name.startswith("networkx."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "networkx" or name.startswith("networkx."):
            raise ModuleNotFoundError("No module named 'networkx'", name="networkx")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.graph.engine")

    assert module.CodebaseGraph.__name__ == "CodebaseGraph"
    with pytest.raises(RuntimeError, match="networkx is required"):
        module.CodebaseGraph()
