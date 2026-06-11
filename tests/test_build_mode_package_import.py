from __future__ import annotations

import builtins
import sys


def test_build_mode_package_import_keeps_lightweight_exports_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.saas.build_mode" or name.startswith("codey.saas.build_mode."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise AssertionError(f"unexpected database dependency import: {name}")
        if name == "anthropic" or name.startswith("anthropic."):
            raise AssertionError(f"unexpected provider dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.build_mode as build_mode
    from codey.saas.build_mode import TaskDecomposer

    assert TaskDecomposer is build_mode.TaskDecomposer
    assert "codey.saas.build_mode.engine" not in sys.modules
    assert "codey.saas.build_mode.generator" not in sys.modules
