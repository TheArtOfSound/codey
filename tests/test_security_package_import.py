from __future__ import annotations

import builtins
import sys


def test_security_package_import_keeps_components_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.saas.security" or name.startswith("codey.saas.security."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise AssertionError(f"unexpected database dependency import: {name}")
        if name == "fastapi" or name.startswith("fastapi."):
            raise AssertionError(f"unexpected web dependency import: {name}")
        if name == "redis" or name.startswith("redis."):
            raise AssertionError(f"unexpected redis dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.security as security

    assert security.__all__ == [
        "AuditLogger",
        "RateLimiter",
        "SecurityMiddleware",
        "decrypt_token",
        "encrypt_token",
        "verify_ownership",
    ]
    assert "codey.saas.security.audit" not in sys.modules
    assert "codey.saas.security.middleware" not in sys.modules
    assert "codey.saas.security.rate_limiter" not in sys.modules
