from __future__ import annotations

import builtins
import sys


def test_sessions_package_import_keeps_components_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.saas.sessions" or name.startswith("codey.saas.sessions."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise AssertionError(f"unexpected database dependency import: {name}")
        if name == "fastapi" or name.startswith("fastapi."):
            raise AssertionError(f"unexpected web dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.sessions as sessions

    assert sessions.__all__ == ["SessionRunner", "SessionStream"]
    assert "codey.saas.sessions.runner" not in sys.modules
    assert "codey.saas.sessions.stream" not in sys.modules
