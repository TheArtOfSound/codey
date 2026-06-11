from __future__ import annotations

import builtins
import importlib
import sys


def test_referrals_import_does_not_require_api_or_database_dependencies(
    monkeypatch,
) -> None:
    monkeypatch.delitem(sys.modules, "codey.saas.referrals", raising=False)
    for module_name in list(sys.modules):
        if module_name == "fastapi" or module_name.startswith("fastapi."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
        if module_name == "sqlalchemy" or module_name.startswith("sqlalchemy."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ModuleNotFoundError("No module named 'fastapi'", name="fastapi")
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise ModuleNotFoundError(
                "No module named 'sqlalchemy'",
                name="sqlalchemy",
            )
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.saas.referrals")

    assert module.REFERRER_CREDITS == 5
    assert module.REFERRED_CREDITS == 3
