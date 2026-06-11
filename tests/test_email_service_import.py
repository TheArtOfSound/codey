from __future__ import annotations

import asyncio
import builtins
import importlib
import sys


def test_email_service_import_does_not_require_sendgrid(monkeypatch) -> None:
    sys.modules.pop("codey.saas.emails.service", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sendgrid" or name.startswith("sendgrid."):
            raise ModuleNotFoundError("No module named 'sendgrid'", name="sendgrid")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.saas.emails.service")

    monkeypatch.setattr(module.settings, "sendgrid_api_key", "   ")
    service = module.EmailService()

    assert service._client is None
    assert asyncio.run(service.send_email("user@example.com", "Hello", "<p>hi</p>")) is False
