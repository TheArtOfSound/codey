from __future__ import annotations

import builtins
import importlib
import sys
from types import SimpleNamespace


def test_websocket_auth_import_does_not_require_fastapi_or_jose(monkeypatch) -> None:
    sys.modules.pop("codey.saas.auth.websockets", None)
    sys.modules.pop("codey.saas.auth.jwt", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ModuleNotFoundError("No module named 'fastapi'", name="fastapi")
        if name == "jose" or name.startswith("jose."):
            raise ModuleNotFoundError("No module named 'jose'", name="jose")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.saas.auth.websockets")

    websocket = SimpleNamespace(query_params={}, cookies={})
    assert module.authenticate_websocket(websocket) is None


def test_websocket_auth_tolerates_missing_token_sources(monkeypatch) -> None:
    module = importlib.import_module("codey.saas.auth.websockets")

    def fail_decode_access_token(_token):
        raise AssertionError("missing token sources should not decode")

    monkeypatch.setattr(module, "decode_access_token", fail_decode_access_token)

    assert module.authenticate_websocket(SimpleNamespace()) is None


def test_websocket_auth_falls_back_when_query_params_get_fails(monkeypatch) -> None:
    module = importlib.import_module("codey.saas.auth.websockets")

    class BrokenQueryParams:
        def get(self, _key):
            raise RuntimeError("query params unavailable")

    monkeypatch.setattr(
        module,
        "decode_access_token",
        lambda token: {"sub": f"user-for-{token}"},
    )
    websocket = SimpleNamespace(
        query_params=BrokenQueryParams(),
        cookies={module.SESSION_COOKIE_NAME: " cookie-token "},
    )

    assert module.authenticate_websocket(websocket) == {"sub": "user-for-cookie-token"}


def test_websocket_auth_rejects_malformed_subject(monkeypatch) -> None:
    module = importlib.import_module("codey.saas.auth.websockets")

    monkeypatch.setattr(
        module,
        "decode_access_token",
        lambda _token: {"sub": ["user-1"]},
    )

    websocket = SimpleNamespace(query_params={"token": "token"}, cookies={})

    assert module.authenticate_websocket(websocket) is None


def test_websocket_auth_normalizes_subject(monkeypatch) -> None:
    module = importlib.import_module("codey.saas.auth.websockets")

    monkeypatch.setattr(
        module,
        "decode_access_token",
        lambda _token: {"sub": " user-1 "},
    )

    websocket = SimpleNamespace(query_params={"token": "token"}, cookies={})

    assert module.authenticate_websocket(websocket) == {"sub": "user-1"}
