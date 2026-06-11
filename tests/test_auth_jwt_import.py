from __future__ import annotations

import builtins
import importlib
import sys


def test_jwt_import_allows_normalization_without_runtime_auth_dependencies(monkeypatch) -> None:
    sys.modules.pop("codey.saas.auth.jwt", None)
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ModuleNotFoundError("No module named 'fastapi'", name="fastapi")
        if name == "jose" or name.startswith("jose."):
            raise ModuleNotFoundError("No module named 'jose'", name="jose")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    jwt_module = importlib.import_module("codey.saas.auth.jwt")

    assert jwt_module.normalize_access_token_candidate(" token ") == "token"
    assert jwt_module.normalize_access_token_candidate("   ") is None
    assert jwt_module._FASTAPI_IMPORT_ERROR is not None
    assert jwt_module._JOSE_IMPORT_ERROR is not None

    try:
        jwt_module.create_access_token("user-1")
    except RuntimeError as exc:
        assert str(exc) == "python-jose is required for JWT auth"
    else:
        raise AssertionError("expected missing python-jose to fail token creation")
