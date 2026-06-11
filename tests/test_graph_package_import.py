from __future__ import annotations

import builtins
import sys


def test_graph_package_import_keeps_engine_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.graph" or name.startswith("codey.graph."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "networkx" or name.startswith("networkx."):
            raise AssertionError(f"unexpected graph dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.graph as graph

    assert graph.__all__ == ["CodebaseGraph"]
    assert "codey.graph.engine" not in sys.modules
