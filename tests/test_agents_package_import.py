from __future__ import annotations

import builtins
import sys


def test_agents_package_import_keeps_orchestrator_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.saas.agents" or name.startswith("codey.saas.agents."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai" or name.startswith("openai."):
            raise AssertionError(f"unexpected provider dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.agents as agents

    assert agents.__all__ == ["AgentOrchestrator"]
    assert "codey.saas.agents.orchestrator" not in sys.modules
