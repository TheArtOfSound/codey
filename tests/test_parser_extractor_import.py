from __future__ import annotations

import builtins
import importlib
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def test_extractor_dataclasses_import_without_tree_sitter(monkeypatch) -> None:
    parent = sys.modules.get("codey.parser")
    monkeypatch.delitem(sys.modules, "codey.parser.extractor", raising=False)
    if parent is not None:
        monkeypatch.delattr(parent, "extractor", raising=False)
    for module_name in list(sys.modules):
        if module_name == "tree_sitter" or module_name.startswith("tree_sitter"):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "tree_sitter" or name.startswith("tree_sitter"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = importlib.import_module("codey.parser.extractor")

    node = module.CodeNode(
        id="node",
        kind="file",
        name="main.py",
        file_path="main.py",
        line_start=1,
        line_end=1,
    )
    assert node.name == "main.py"
    assert module.parse_directory(Path("/definitely/missing")) == ([], [])

    with pytest.raises(RuntimeError, match="tree-sitter packages are required"):
        module.LanguageParser()


def test_extract_from_source_normalizes_malformed_filenames(monkeypatch) -> None:
    module = importlib.import_module("codey.parser.extractor")
    parsed_names: list[str] = []

    class DummyParser:
        def parse_file(self, path: Path):
            parsed_names.append(path.name)
            return [SimpleNamespace(file_path="temporary")], []

    monkeypatch.setattr(module, "LanguageParser", DummyParser)

    nodes, edges = module.extract_from_source(
        "print('ok')",
        "../secret.py",
        "python",
    )

    assert parsed_names == ["snippet.py"]
    assert nodes[0].file_path == "snippet.py"
    assert edges == []


def test_extract_from_source_applies_language_suffix_to_extensionless_paths(
    monkeypatch,
) -> None:
    module = importlib.import_module("codey.parser.extractor")
    parsed_names: list[str] = []

    class DummyParser:
        def parse_file(self, path: Path):
            parsed_names.append(path.name)
            return [SimpleNamespace(file_path="temporary")], []

    monkeypatch.setattr(module, "LanguageParser", DummyParser)

    nodes, _edges = module.extract_from_source(
        "export const value = 1",
        ".\\src\\app",
        "typescript",
    )

    assert parsed_names == ["app.ts"]
    assert nodes[0].file_path == "src/app.ts"
    assert module._normalize_source_filename("main", None) == "main.py"


def test_parse_file_redacts_tree_sitter_failure_logs(
    tmp_path,
    caplog,
) -> None:
    module = importlib.import_module("codey.parser.extractor")

    class FailingParser:
        def parse(self, source_bytes):
            raise RuntimeError(
                "parse failed "
                "https://user:secret@example.test/tree?api_key=parse-secret&client_secret=client123 "
                "access_token=abc123 auth_token=auth123 refresh_token=refresh123 "
                "password=pw123 for user@example.com authorization=Bearer bearer123"
            )

    parser = module.LanguageParser.__new__(module.LanguageParser)
    parser._parsers = {".py": FailingParser()}
    parser._js_extensions = set()

    source = tmp_path / "pkg-user@example.com?token=file-token.py"
    source.write_text("print('ok')", encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="codey.parser.extractor")

    nodes, edges = parser.parse_file(source)

    assert nodes == []
    assert edges == []
    assert "user@example.com" not in caplog.text
    assert "user:secret" not in caplog.text
    assert "secret@example.test" not in caplog.text
    assert "parse-secret" not in caplog.text
    assert "client123" not in caplog.text
    assert "file-token" not in caplog.text
    assert "abc123" not in caplog.text
    assert "auth123" not in caplog.text
    assert "refresh123" not in caplog.text
    assert "pw123" not in caplog.text
    assert "bearer123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/tree?api_key=***&client_secret=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "auth_token=***" in caplog.text
    assert "refresh_token=***" in caplog.text
    assert "password=***" in caplog.text
    assert "authorization=Bearer ***" in caplog.text
    assert "Traceback" not in caplog.text


def test_parse_directory_skips_common_virtualenv_dirs(monkeypatch, tmp_path) -> None:
    module = importlib.import_module("codey.parser.extractor")
    parsed_paths: list[str] = []

    class DummyParser:
        def parse_file(self, path: Path):
            parsed_paths.append(path.name)
            return [], []

    (tmp_path / "venv" / "pkg").mkdir(parents=True)
    (tmp_path / "venv" / "pkg" / "ignored.py").write_text(
        "print('skip')",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("print('parse')", encoding="utf-8")

    monkeypatch.setattr(module, "LanguageParser", DummyParser)

    nodes, edges = module.parse_directory(tmp_path)

    assert nodes == []
    assert edges == []
    assert parsed_paths == ["app.py"]


def test_parse_directory_does_not_apply_skip_dirs_to_parent_path(
    monkeypatch,
    tmp_path,
) -> None:
    module = importlib.import_module("codey.parser.extractor")
    parsed_paths: list[str] = []

    class DummyParser:
        def parse_file(self, path: Path):
            parsed_paths.append(path.name)
            return [], []

    root = tmp_path / "build" / "repo"
    root.mkdir(parents=True)
    (root / "app.py").write_text("print('parse')", encoding="utf-8")

    monkeypatch.setattr(module, "LanguageParser", DummyParser)

    nodes, edges = module.parse_directory(root)

    assert nodes == []
    assert edges == []
    assert parsed_paths == ["app.py"]


def test_parse_directory_prunes_skipped_dirs_without_rglob(
    monkeypatch,
    tmp_path,
) -> None:
    module = importlib.import_module("codey.parser.extractor")
    parsed_paths: list[str] = []

    class DummyParser:
        def parse_file(self, path: Path):
            parsed_paths.append(path.name)
            return [], []

    def fail_rglob(_self, _pattern):
        raise AssertionError("parse_directory should prune with os.walk")

    (tmp_path / "venv" / "pkg").mkdir(parents=True)
    (tmp_path / "venv" / "pkg" / "ignored.py").write_text(
        "print('skip')",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("print('parse')", encoding="utf-8")

    monkeypatch.setattr(module, "LanguageParser", DummyParser)
    monkeypatch.setattr(Path, "rglob", fail_rglob)

    nodes, edges = module.parse_directory(tmp_path)

    assert nodes == []
    assert edges == []
    assert parsed_paths == ["app.py"]
