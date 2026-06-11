from __future__ import annotations

import builtins
import sys


def test_nfet_package_import_keeps_state_exports_lightweight(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.nfet" or name.startswith("codey.nfet."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "numpy" or name.startswith("numpy."):
            raise AssertionError(f"unexpected numeric dependency import: {name}")
        if name == "scipy" or name.startswith("scipy."):
            raise AssertionError(f"unexpected numeric dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.nfet as nfet
    from codey.nfet import NodeState, RepoState

    assert NodeState is nfet.NodeState
    assert RepoState is nfet.RepoState
    assert "codey.nfet.sweep" not in sys.modules
    assert "codey.nfet.health_db" not in sys.modules
