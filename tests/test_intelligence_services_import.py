from __future__ import annotations

import asyncio
import builtins
import importlib
import sys

import pytest


def test_intelligence_services_no_key_paths_import_without_httpx(monkeypatch) -> None:
    parent = sys.modules.get("codey.saas.intelligence")
    monkeypatch.delitem(
        sys.modules, "codey.saas.intelligence.services", raising=False
    )
    if parent is not None:
        monkeypatch.delattr(parent, "services", raising=False)
    for module_name in list(sys.modules):
        if module_name == "httpx" or module_name.startswith("httpx."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "httpx" or name.startswith("httpx."):
            raise ModuleNotFoundError("No module named 'httpx'", name="httpx")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    for env_name in (
        "TAVILY_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "EXA_API_KEY",
        "BING_SEARCH_API_KEY",
        "PERPLEXITY_API_KEY",
    ):
        monkeypatch.setenv(env_name, "   ")

    module = importlib.import_module("codey.saas.intelligence.services")

    svc = module.IntelligenceServices()
    try:
        assert asyncio.run(svc.search_tavily("cache invalidation")) is None
        assert asyncio.run(svc.search_brave("cache invalidation")) is None
        assert asyncio.run(svc.search_exa("cache invalidation")) is None
        assert asyncio.run(svc.search_bing("cache invalidation")) is None
        assert asyncio.run(svc.search_perplexity("cache invalidation")) is None
    finally:
        asyncio.run(svc.close())

    missing_httpx = "httpx is required for intelligence service network calls"
    with pytest.raises(RuntimeError, match=missing_httpx):
        module._require_httpx()
