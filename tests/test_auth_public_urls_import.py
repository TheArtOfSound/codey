from __future__ import annotations

import builtins
import sys


def test_public_urls_import_does_not_require_fastapi(monkeypatch) -> None:
    sys.modules.pop("codey.saas.auth.public_urls", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "fastapi" or name.startswith("fastapi."):
            raise AssertionError(f"unexpected web dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.auth.public_urls as public_urls

    assert public_urls.get_public_frontend_origin(None)
    assert public_urls.get_public_api_base_url(None)
