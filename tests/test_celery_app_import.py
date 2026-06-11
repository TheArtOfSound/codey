from __future__ import annotations

import builtins
import importlib
import sys

import pytest


def test_celery_app_imports_without_celery(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "codey.saas.tasks.celery_app", raising=False)
    for module_name in list(sys.modules):
        if module_name == "celery" or module_name.startswith("celery."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "celery" or name.startswith("celery."):
            raise ModuleNotFoundError("No module named 'celery'", name="celery")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.saas.tasks.celery_app")

    @module.celery_app.task(bind=True, name="sample")
    def sample(self, value: int) -> int:
        return value + 1

    assert sample.run(1) == 2
    assert module.celery_app.conf.task_serializer == "json"
    assert module.celery_app.conf.beat_schedule["scheduled-autonomous-repos"]

    with pytest.raises(RuntimeError, match="celery is required"):
        sample.apply_async(args=[1])


def test_celery_app_reraises_ambiguous_module_not_found(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "codey.saas.tasks.celery_app", raising=False)
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "celery":
            raise ModuleNotFoundError("No module named transitive_dependency")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    with pytest.raises(ModuleNotFoundError, match="transitive_dependency"):
        importlib.import_module("codey.saas.tasks.celery_app")
