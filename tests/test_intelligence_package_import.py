from __future__ import annotations

import builtins
import sys


def test_intelligence_package_import_is_lightweight(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.saas.intelligence" or name.startswith(
            "codey.saas.intelligence."
        ):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai" or name.startswith("openai."):
            raise AssertionError(f"unexpected optional dependency import: {name}")
        if name == "redis" or name.startswith("redis."):
            raise AssertionError(f"unexpected optional dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.intelligence as intelligence

    assert intelligence.IntelligenceStack.__name__ == "IntelligenceStack"
    assert "codey.saas.intelligence.ensemble" not in sys.modules
    assert "codey.saas.intelligence.services" not in sys.modules
