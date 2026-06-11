from __future__ import annotations

import builtins
import sys


def test_parser_package_import_keeps_extractor_lazy(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "codey.parser" or name.startswith("codey.parser."):
            sys.modules.pop(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "tree_sitter" or name.startswith("tree_sitter."):
            raise AssertionError(f"unexpected parser dependency import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.parser as parser

    assert parser.__all__ == [
        "CodeNode",
        "CodeEdge",
        "LanguageParser",
        "parse_directory",
    ]
    assert "codey.parser.extractor" not in sys.modules
