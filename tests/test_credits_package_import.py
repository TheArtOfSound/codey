from __future__ import annotations

import builtins
import sys


def test_credits_package_import_keeps_service_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.saas.credits" or name.startswith("codey.saas.credits."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise AssertionError(f"unexpected database dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.credits as credits

    assert credits.__all__ == [
        "CreditService",
        "InsufficientCreditsError",
        "CREDIT_COSTS",
        "PLAN_CREDITS",
    ]
    assert "codey.saas.credits.service" not in sys.modules
