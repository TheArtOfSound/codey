from __future__ import annotations

import builtins
import sys


def test_dashboard_package_import_keeps_server_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.dashboard" or name.startswith("codey.dashboard."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "uvicorn" or name.startswith("uvicorn."):
            raise AssertionError(f"unexpected server dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.dashboard as dashboard

    assert dashboard.__all__ == ["create_app", "DashboardState"]
    assert "codey.dashboard.server" not in sys.modules
