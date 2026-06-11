from __future__ import annotations

import asyncio
import builtins
import importlib
import sys

import pytest


def test_research_import_does_not_require_httpx(monkeypatch) -> None:
    sys.modules.pop("codey.saas.intelligence.research", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "httpx" or name.startswith("httpx."):
            raise ModuleNotFoundError("No module named 'httpx'", name="httpx")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    for name in ("TAVILY_API_KEY", "BRAVE_API_KEY", "EXA_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    module = importlib.import_module("codey.saas.intelligence.research")
    engine = module.ResearchEngine()

    assert isinstance(module.LibraryInfo(name="httpx").vulnerabilities, list)
    assert asyncio.run(engine.search_web("python")) == []
    assert asyncio.run(engine.search_code("async client")) == []
    with pytest.raises(RuntimeError, match="httpx is required for research network calls"):
        module._require_httpx()
