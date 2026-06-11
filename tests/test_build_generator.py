from __future__ import annotations

import pytest

import codey.saas.build_mode.generator as generator_module

from codey.saas.build_mode.decomposer import TaskNode
from codey.saas.build_mode.generator import BuildContext, FileGenerator


def test_build_generation_prompt_tolerates_malformed_project_plan() -> None:
    generator = FileGenerator()
    task = TaskNode(file_path="app/main.py", phase=2, dependencies=["app/deps.py"])
    context = BuildContext(
        project_plan={
            "name": "Demo App",
            "description": "Example build",
            "stack": ["broken-shape"],
            "file_tree": ["not", "a", "mapping"],
            "phases": [None, "skip-me", {"description": "Create the entrypoint"}],
        },
        generated_files={"app/deps.py": "def helper():\n    return 1\n"},
    )

    system_prompt, messages = generator._build_generation_prompt(task, context)

    assert "not specified application" in system_prompt
    assert len(messages) == 1
    assert "Demo App" in messages[0]["content"]
    assert "Create the entrypoint" in messages[0]["content"]
    assert "app/deps.py" in messages[0]["content"]


def test_build_generation_prompt_preserves_nfet_section_when_trimming_without_summaries(
    monkeypatch,
) -> None:
    monkeypatch.setattr(generator_module, "_MAX_CONTEXT_CHARS", 10)

    generator = FileGenerator()
    task = TaskNode(file_path="app/main.py", phase=0)
    context = BuildContext(project_plan={"name": "Demo App"})

    _, messages = generator._build_generation_prompt(task, context)
    content = messages[0]["content"]

    assert "## NFET STATE" in content
    assert "## NOW BUILD: `app/main.py`" in content
    assert "## ALREADY BUILT (summaries)" not in content


def test_format_plan_summary_normalizes_string_phase_files() -> None:
    generator = FileGenerator()

    summary = generator._format_plan_summary(
        {
            "phases": [
                {
                    "name": "Core",
                    "description": "Build core",
                    "files": "app/main.py",
                }
            ]
        }
    )

    assert "(1 files)" in summary


def test_count_generated_file_lines_ignores_blank_and_trailing_lines() -> None:
    assert generator_module._count_generated_file_lines("") == 0
    assert generator_module._count_generated_file_lines("print('ok')\n") == 1
    assert (
        generator_module._count_generated_file_lines(
            "\nprint('one')\n\nprint('two')\n"
        )
        == 2
    )


@pytest.mark.asyncio
async def test_generate_file_accepts_mapping_model_output(monkeypatch) -> None:
    generator = FileGenerator()
    task = TaskNode(file_path="app/main.py", phase=0)
    context = BuildContext(project_plan={"name": "Demo App"})

    async def fake_call_model(provider, model, messages, **kwargs):
        assert provider == "stub"
        assert model == "stub"
        assert messages[0]["role"] == "system"
        return {
            "content": "```python\nfrom fastapi import FastAPI\n\napp = FastAPI()\n```\n",
        }

    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fake_call_model,
    )

    generated = await generator.generate_file(task, context)

    assert generated.path == "app/main.py"
    assert generated.content == "from fastapi import FastAPI\n\napp = FastAPI()"
    assert generated.line_count == 2
    assert generated.summary.line_count == 2


def test_parse_file_content_rejects_unsupported_mapping_output() -> None:
    generator = FileGenerator()

    with pytest.raises(TypeError, match="non-text file content"):
        generator._parse_file_content(
            {"unexpected": {"nested": "value"}},
            "app/main.py",
        )


def test_parse_file_content_accepts_structured_text_blocks() -> None:
    generator = FileGenerator()

    content = generator._parse_file_content(
        {
            "content": [
                {"type": "text", "text": "Here is the file:"},
                {"type": "text", "text": "```python\nprint('ok')\n```"},
                {"type": "image", "source": "ignored"},
            ]
        },
        "app/main.py",
    )

    assert content == "print('ok')"


def test_parse_file_content_accepts_fence_metadata() -> None:
    generator = FileGenerator()

    content = generator._parse_file_content(
        '```python title="app/main.py"\r\nprint("ok")\r\n```',
        "app/main.py",
    )

    assert content == 'print("ok")'


def test_parse_file_content_rejects_empty_output() -> None:
    generator = FileGenerator()

    for value in (" \n\t", "```python\n \n```"):
        with pytest.raises(TypeError, match="empty file content"):
            generator._parse_file_content(value, "app/main.py")


def test_parse_file_content_falls_back_across_mapping_text_fields() -> None:
    generator = FileGenerator()

    content = generator._parse_file_content(
        {
            "content": [],
            "text": "```python\nprint('fallback')\n```",
        },
        "app/main.py",
    )

    assert content == "print('fallback')"
