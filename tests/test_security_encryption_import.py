from __future__ import annotations

import builtins
import importlib
import sys

import pytest


def test_encryption_import_does_not_require_crypto_dependencies(monkeypatch) -> None:
    sys.modules.pop("codey.saas.security.encryption", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "bcrypt" or name.startswith("bcrypt."):
            raise ModuleNotFoundError("No module named 'bcrypt'", name="bcrypt")
        if name == "cryptography" or name.startswith("cryptography."):
            raise ModuleNotFoundError("No module named 'cryptography'", name="cryptography")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.saas.security.encryption")

    with pytest.raises(RuntimeError, match="cryptography is required for token encryption"):
        module.encrypt_token("secret")
    with pytest.raises(RuntimeError, match="bcrypt is required for API key hashing"):
        module.hash_api_key("cdy_test")
