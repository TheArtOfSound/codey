from __future__ import annotations

import builtins
import importlib
import sys

import pytest


def test_oauth_import_does_not_require_runtime_auth_dependencies(monkeypatch) -> None:
    sys.modules.pop("codey.saas.auth.oauth", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "httpx" or name.startswith("httpx."):
            raise ModuleNotFoundError("No module named 'httpx'", name="httpx")
        if name == "fastapi" or name.startswith("fastapi."):
            raise ModuleNotFoundError("No module named 'fastapi'", name="fastapi")
        if name == "jose" or name.startswith("jose."):
            raise ModuleNotFoundError("No module named 'jose'", name="jose")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.saas.auth.oauth")

    assert module._normalize_oauth_api_base_url(" https://api.example.com/v1/ ") == (
        "https://api.example.com/v1"
    )
    with pytest.raises(ValueError, match="Invalid GitHub OAuth intent"):
        module.oauth_github_url(intent="bogus")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="python-jose is required for OAuth state auth"):
        module.oauth_github_url()
    with pytest.raises(RuntimeError, match="httpx is required for OAuth provider calls"):
        module._require_httpx()
