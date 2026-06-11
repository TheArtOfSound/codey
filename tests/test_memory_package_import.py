from __future__ import annotations

import builtins
import sys


def test_memory_package_import_keeps_engine_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.saas.memory" or name.startswith("codey.saas.memory."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise AssertionError(f"unexpected database dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.memory as memory

    assert memory.__all__ == ["MemoryEngine"]
    assert "codey.saas.memory.engine" not in sys.modules
