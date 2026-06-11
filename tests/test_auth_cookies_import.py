from __future__ import annotations

import builtins
import sys


def test_cookies_import_does_not_require_fastapi(monkeypatch) -> None:
    sys.modules.pop("codey.saas.auth.cookies", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "fastapi" or name.startswith("fastapi."):
            raise AssertionError(f"unexpected web dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.auth.cookies as cookies

    assert cookies.SESSION_COOKIE_NAME == "codey_session"
    assert cookies._cookie_is_secure("https://app.example.com")
