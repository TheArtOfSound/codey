from __future__ import annotations

import asyncio
import builtins
import importlib
import sys

import pytest


def test_embeddings_import_does_not_require_httpx_or_sqlalchemy(monkeypatch) -> None:
    sys.modules.pop("codey.saas.intelligence.embeddings", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "httpx" or name.startswith("httpx."):
            raise ModuleNotFoundError("No module named 'httpx'", name="httpx")
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise ModuleNotFoundError("No module named 'sqlalchemy'", name="sqlalchemy")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setenv("COHERE_API_KEY", "   ")
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "   ")

    module = importlib.import_module("codey.saas.intelligence.embeddings")
    service = module.EmbeddingService()

    assert module._coerce_positive_rowcount("2") is True
    assert asyncio.run(service.embed_single("semantic search")) is None
    asyncio.run(service.close())

    with pytest.raises(RuntimeError, match="httpx is required for embedding provider calls"):
        module._require_httpx()
    with pytest.raises(RuntimeError, match="SQLAlchemy is required for embedding persistence"):
        module._require_sqlalchemy()
