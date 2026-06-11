from __future__ import annotations

import builtins
import sys


def test_models_package_import_keeps_model_modules_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.saas.models" or name.startswith("codey.saas.models."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise AssertionError(f"unexpected database dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.models as models

    assert "User" in models.__all__
    assert "BuildProject" in models.__all__
    assert "codey.saas.models.user" not in sys.modules
    assert "codey.saas.models.build_project" not in sys.modules
