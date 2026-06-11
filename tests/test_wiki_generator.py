from __future__ import annotations

import io
from pathlib import Path

import pytest

import codey.saas.wiki.generator as wiki_generator_module
from codey.saas.wiki.generator import ProjectWiki


def test_wiki_scanners_skip_symlinked_python_files_outside_project(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside.py"
    outside.write_text(
        "@app.get('/secret')\n"
        "def secret():\n"
        "    return {'secret': True}\n\n"
        "class Leaked:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "leak.py").symlink_to(outside)

    wiki = ProjectWiki()

    assert wiki._extract_code_summary(tmp_path) == ""
    assert wiki._extract_routes(tmp_path) == []


def test_extract_env_vars_skips_symlinked_env_example_outside_project(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_env.example"
    outside.write_text("LEAKED_SECRET=1\n", encoding="utf-8")
    (tmp_path / ".env.example").symlink_to(outside)

    assert ProjectWiki()._extract_env_vars(tmp_path) == []


def test_extract_dependencies_skips_symlinked_manifests_outside_project(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_requirements.txt"
    outside.write_text("requests\n", encoding="utf-8")
    (tmp_path / "requirements.txt").symlink_to(outside)

    assert ProjectWiki()._extract_dependencies(tmp_path) == {}


def test_read_safe_project_text_rejects_file_that_grows_after_stat(
    monkeypatch,
    tmp_path,
) -> None:
    path = tmp_path / "app.py"
    path.write_text("ok", encoding="utf-8")
    original_open = Path.open

    def grow_after_stat(self: Path, *args, **kwargs):
        if self == path:
            return io.StringIO("x" * (wiki_generator_module.MAX_FILE_SIZE + 1))
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", grow_after_stat)

    assert wiki_generator_module._read_safe_project_text(path, tmp_path) == ""


def test_wiki_scanners_use_safe_bounded_reader(monkeypatch, tmp_path) -> None:
    route_file = tmp_path / "routes.py"
    route_file.write_text(
        "@app.get('/health')\ndef health():\n    pass\n",
        encoding="utf-8",
    )

    def fail_read_text(*args, **kwargs):
        raise AssertionError("wiki scanners should use _read_safe_project_text")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    routes = ProjectWiki()._extract_routes(tmp_path)

    assert [route["path"] for route in routes] == ["/health"]


@pytest.mark.asyncio
async def test_update_ignores_changed_paths_outside_project(monkeypatch, tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside.py"
    outside.write_text("@app.get('/secret')\ndef secret():\n    pass\n", encoding="utf-8")

    async def fake_call_model(provider, model, messages, **kwargs):
        raise AssertionError("No model call expected when all paths are invalid")

    monkeypatch.setattr(wiki_generator_module, "call_model", fake_call_model)

    diff = await ProjectWiki().update(
        str(tmp_path),
        [f"../{outside.name}", "/tmp/absolute.py"],
    )

    assert diff.modified_sections == []
    assert diff.raw_diff == "No visible documentation changes were detected."


@pytest.mark.asyncio
async def test_update_normalizes_changed_file_paths(monkeypatch, tmp_path) -> None:
    routes_dir = tmp_path / "api"
    routes_dir.mkdir()
    route_file = routes_dir / "routes.py"
    route_file.write_text(
        "@app.get('/health')\nasync def health():\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    captured_prompt = ""

    monkeypatch.setattr(
        wiki_generator_module,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )

    async def fake_call_model(provider, model, messages, **kwargs):
        nonlocal captured_prompt
        captured_prompt = messages[0]["content"]
        return {"content": "Update API docs."}

    monkeypatch.setattr(wiki_generator_module, "call_model", fake_call_model)

    diff = await ProjectWiki().update(
        str(tmp_path),
        ["api\\routes.py", "bad\nmodel.py", {"path": "ignored.py"}],
    )

    assert diff.modified_sections == ["API Reference"]
    assert "api/routes.py" in captured_prompt
    assert "bad\nmodel.py" not in captured_prompt
    assert "ignored.py" not in captured_prompt


@pytest.mark.asyncio
async def test_generate_architecture_section_accepts_mapping_model_output(
    monkeypatch,
) -> None:
    wiki = ProjectWiki()

    monkeypatch.setattr(
        wiki_generator_module,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )

    async def fake_call_model(provider, model, messages, **kwargs):
        assert provider == "stub"
        assert model == "stub"
        return {"content": "Architecture summary"}

    monkeypatch.setattr(wiki_generator_module, "call_model", fake_call_model)

    section = await wiki._generate_architecture_section(
        "demo-project",
        "app/\n  main.py",
        "FastAPI app",
        {},
    )

    assert section.title == "Architecture Overview"
    assert section.content == "Architecture summary"


@pytest.mark.asyncio
async def test_generate_architecture_section_falls_back_on_blank_model_output(
    monkeypatch,
) -> None:
    wiki = ProjectWiki()

    monkeypatch.setattr(
        wiki_generator_module,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )

    async def fake_call_model(provider, model, messages, **kwargs):
        return {"content": "   "}

    monkeypatch.setattr(wiki_generator_module, "call_model", fake_call_model)

    section = await wiki._generate_architecture_section(
        "demo-project",
        "app/\n  main.py",
        "FastAPI app",
        {},
    )

    assert section.title == "Architecture Overview"
    assert "demo-project" in section.content
    assert "FastAPI app" in section.content


@pytest.mark.asyncio
async def test_update_accepts_mapping_diff_summary(monkeypatch, tmp_path) -> None:
    wiki = ProjectWiki()
    route_file = tmp_path / "api_routes.py"
    route_file.write_text(
        "@app.get('/health')\nasync def health():\n    return {'ok': True}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        wiki_generator_module,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )

    async def fake_call_model(provider, model, messages, **kwargs):
        assert provider == "stub"
        assert model == "stub"
        return {"content": "Update the API Reference section."}

    monkeypatch.setattr(wiki_generator_module, "call_model", fake_call_model)

    diff = await wiki.update(str(tmp_path), ["api_routes.py"])

    assert diff.modified_sections == ["API Reference"]
    assert diff.raw_diff == "Update the API Reference section."


@pytest.mark.asyncio
async def test_update_falls_back_on_blank_diff_summary(monkeypatch, tmp_path) -> None:
    wiki = ProjectWiki()
    route_file = tmp_path / "api_routes.py"
    route_file.write_text(
        "@app.get('/health')\nasync def health():\n    return {'ok': True}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        wiki_generator_module,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )

    async def fake_call_model(provider, model, messages, **kwargs):
        return {"content": " \n\t"}

    monkeypatch.setattr(wiki_generator_module, "call_model", fake_call_model)

    diff = await wiki.update(str(tmp_path), ["api_routes.py"])

    assert diff.modified_sections == ["API Reference"]
    assert diff.raw_diff == "Documentation summary unavailable. Review changes in: api_routes.py"
