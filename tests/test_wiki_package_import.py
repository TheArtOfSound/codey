from __future__ import annotations

import builtins
import sys


def test_wiki_package_import_keeps_generator_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.saas.wiki" or name.startswith("codey.saas.wiki."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai" or name.startswith("openai."):
            raise AssertionError(f"unexpected provider dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.wiki as wiki

    assert wiki.__all__ == ["ProjectWiki"]
    assert "codey.saas.wiki.generator" not in sys.modules
