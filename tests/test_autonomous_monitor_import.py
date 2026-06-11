from __future__ import annotations

import builtins
import importlib
from pathlib import Path
import sys

import pytest


def test_monitor_imports_without_watchdog_until_start(monkeypatch) -> None:
    parent = sys.modules.get("codey.autonomous")
    monkeypatch.delitem(sys.modules, "codey.autonomous.monitor", raising=False)
    if parent is not None:
        monkeypatch.delattr(parent, "monitor", raising=False)
    for module_name in list(sys.modules):
        if module_name == "watchdog" or module_name.startswith("watchdog."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "watchdog" or name.startswith("watchdog."):
            raise ModuleNotFoundError(f"No module named {name!r}", name="watchdog")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.autonomous.monitor")

    assert module.TriggerCondition.LINT_ERROR.value == "lint_error"

    monitor = module.AutonomousMonitor.__new__(module.AutonomousMonitor)
    monitor._running = False

    with pytest.raises(RuntimeError, match="watchdog is required"):
        module.AutonomousMonitor.start(monitor, Path("."))
    assert monitor._running is False
