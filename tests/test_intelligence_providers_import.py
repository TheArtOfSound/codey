from __future__ import annotations

import builtins
import importlib
import sys

import pytest


def test_providers_import_does_not_require_openai(monkeypatch) -> None:
    sys.modules.pop("codey.saas.intelligence.providers", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai" or name.startswith("openai."):
            raise ModuleNotFoundError("No module named 'openai'", name="openai")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.saas.intelligence.providers")

    monkeypatch.setenv("GROQ_API_KEY", "   ")
    assert "groq" not in module.get_available_providers()
    with pytest.raises(ValueError, match="Unknown provider"):
        module.get_client("missing-provider")

    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    with pytest.raises(RuntimeError, match="openai is required for AI provider clients"):
        module.get_client("groq")
