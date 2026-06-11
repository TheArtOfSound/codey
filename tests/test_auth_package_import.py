from __future__ import annotations

import builtins
import sys


def test_auth_package_import_keeps_components_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.saas.auth" or name.startswith("codey.saas.auth."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "fastapi" or name.startswith("fastapi."):
            raise AssertionError(f"unexpected web dependency import: {name}")
        if name == "jose" or name.startswith("jose."):
            raise AssertionError(f"unexpected jwt dependency import: {name}")
        if name == "pydantic_settings" or name.startswith("pydantic_settings."):
            raise AssertionError(f"unexpected settings dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.auth as auth

    assert auth.__all__ == [
        "AuthService",
        "create_access_token",
        "get_current_user",
        "oauth_github_url",
        "oauth_google_url",
    ]
    assert "codey.saas.auth.dependencies" not in sys.modules
    assert "codey.saas.auth.jwt" not in sys.modules
    assert "codey.saas.auth.service" not in sys.modules
