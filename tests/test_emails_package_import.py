from __future__ import annotations

import builtins
import sys


def test_emails_package_import_keeps_service_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.saas.emails" or name.startswith("codey.saas.emails."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sendgrid" or name.startswith("sendgrid."):
            raise AssertionError(f"unexpected email provider dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.emails as emails

    assert emails.__all__ == ["EmailService"]
    assert "codey.saas.emails.service" not in sys.modules
