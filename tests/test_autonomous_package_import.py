from __future__ import annotations

import builtins
import sys


def test_autonomous_package_import_keeps_monitor_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.autonomous" or name.startswith("codey.autonomous."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "watchdog" or name.startswith("watchdog."):
            raise AssertionError(f"unexpected monitor dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.autonomous as autonomous

    assert autonomous.__all__ == [
        "AutonomousMonitor",
        "AuditDatabase",
        "TriggerCondition",
    ]
    assert "codey.autonomous.monitor" not in sys.modules
