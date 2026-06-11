from __future__ import annotations

import asyncio
import builtins
import importlib
import sys

import pytest


def test_stripe_setup_import_does_not_require_stripe(monkeypatch) -> None:
    sys.modules.pop("codey.saas.billing.stripe_setup", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "stripe" or name.startswith("stripe."):
            raise ModuleNotFoundError("No module named 'stripe'", name="stripe")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.saas.billing.stripe_setup")

    assert module._metadata_lookup({"codey_entity": "plan_pro"}, "codey_entity") == "plan_pro"
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_missing_sdk")
    monkeypatch.setattr(module.settings, "stripe_secret_key", "   ")

    with pytest.raises(RuntimeError, match="stripe is required for Stripe catalog setup"):
        asyncio.run(module.setup_stripe_products())
