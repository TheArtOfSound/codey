from __future__ import annotations

import builtins
import sys


def test_llm_package_import_keeps_components_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.llm" or name.startswith("codey.llm."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "networkx" or name.startswith("networkx."):
            raise AssertionError(f"unexpected graph dependency import: {name}")
        if name == "numpy" or name.startswith("numpy."):
            raise AssertionError(f"unexpected numeric dependency import: {name}")
        if name == "anthropic" or name.startswith("anthropic."):
            raise AssertionError(f"unexpected provider dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.llm as llm

    assert llm.__all__ == ["PromptBuilder", "CodeAgent"]
    assert "codey.llm.code_agent" not in sys.modules
    assert "codey.llm.prompt_builder" not in sys.modules
