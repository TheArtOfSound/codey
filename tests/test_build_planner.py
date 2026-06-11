from __future__ import annotations

import json

import pytest

from codey.saas.build_mode.planner import ProjectPlanner


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"name": "ok"}', {"name": "ok"}),
        ("[]", {}),
        ('"hello"', {}),
        ("```json\n[]\n```", {}),
    ],
)
def test_extract_json_returns_mapping_only(raw: str, expected: dict[str, str]) -> None:
    planner = ProjectPlanner()

    assert planner._extract_json(raw) == expected


def test_validate_plan_normalizes_phase_file_shapes() -> None:
    planner = ProjectPlanner()

    plan = planner._validate_plan(
        {
            "file_tree": {},
            "phases": [
                None,
                {"files": "app/main.py"},
                {"files": ["app/routes.py", None, 5, "  "]},
                "skip-me",
            ],
        }
    )

    assert [phase["files"] for phase in plan["phases"]] == [
        ["app/main.py"],
        ["app/routes.py"],
    ]
    assert plan["file_tree"] == {
        "app/main.py": "service",
        "app/routes.py": "service",
    }


def test_validate_plan_rejects_unsafe_file_paths() -> None:
    planner = ProjectPlanner()

    plan = planner._validate_plan(
        {
            "file_tree": {
                "/tmp/secret.py": "service",
                "../secret.py": "service",
                "C:\\tmp\\secret.py": "service",
                "bad\x00name.py": "service",
                "./app//main.py": "service",
                "app\\models\\.\\user.py": "model",
                "app/../escape.py": "service",
            },
            "phases": [
                {
                    "files": [
                        "../secret.py",
                        "/tmp/secret.py",
                        "C:/tmp/secret.py",
                        "./app//main.py",
                        "app\\models\\.\\user.py",
                        "app/../escape.py",
                    ],
                }
            ],
        }
    )

    assert plan["file_tree"] == {
        "app/main.py": "service",
        "app/models/user.py": "model",
    }
    assert plan["phases"][0]["files"] == ["app/main.py", "app/models/user.py"]


@pytest.mark.asyncio
async def test_project_planner_clarify_uses_model_provider(monkeypatch) -> None:
    planner = ProjectPlanner()
    monkeypatch.setattr(planner, "_match_template", lambda _description: None)

    async def fake_call_model(provider, model, messages, **kwargs) -> str:
        assert provider == "stub"
        assert model == "stub"
        assert messages[-1]["role"] == "user"
        return json.dumps(
            {
                "questions": ["Need auth?"],
                "defaults": {"Need auth?": "Email/password"},
            }
        )

    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fake_call_model,
    )

    result = await planner.clarify("Build an API")

    assert result == {
        "questions": ["Need auth?"],
        "defaults": {"Need auth?": "Email/password"},
        "template_match": None,
    }


@pytest.mark.asyncio
async def test_project_planner_clarify_normalizes_single_question_shape(monkeypatch) -> None:
    planner = ProjectPlanner()
    monkeypatch.setattr(planner, "_match_template", lambda _description: None)

    async def fake_call_model(provider, model, messages, **kwargs) -> str:
        assert provider == "stub"
        assert model == "stub"
        assert messages[-1]["role"] == "user"
        return json.dumps(
            {
                "questions": "Need auth?",
                "defaults": {
                    "Need auth?": "Email/password",
                    "Ignore me": 3,
                },
            }
        )

    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fake_call_model,
    )

    result = await planner.clarify("Build an API")

    assert result == {
        "questions": ["Need auth?"],
        "defaults": {"Need auth?": "Email/password"},
        "template_match": None,
    }


@pytest.mark.asyncio
async def test_project_planner_clarify_accepts_mapping_model_output(monkeypatch) -> None:
    planner = ProjectPlanner()
    monkeypatch.setattr(planner, "_match_template", lambda _description: None)

    async def fake_call_model(provider, model, messages, **kwargs):
        assert provider == "stub"
        assert model == "stub"
        assert messages[-1]["role"] == "user"
        return {
            "questions": ["Need auth?"],
            "defaults": {"Need auth?": "Email/password"},
        }

    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fake_call_model,
    )

    result = await planner.clarify("Build an API")

    assert result == {
        "questions": ["Need auth?"],
        "defaults": {"Need auth?": "Email/password"},
        "template_match": None,
    }


@pytest.mark.asyncio
async def test_project_planner_create_plan_uses_model_provider(monkeypatch) -> None:
    planner = ProjectPlanner()
    monkeypatch.setattr(planner, "_match_template", lambda _description: None)

    async def fake_call_model(provider, model, messages, **kwargs) -> str:
        assert provider == "stub"
        assert model == "stub"
        assert messages[-1]["role"] == "user"
        return json.dumps(
            {
                "name": "Demo App",
                "description": "Generated plan",
                "stack": {"backend": "FastAPI"},
                "file_tree": {"app/main.py": "service"},
                "phases": [
                    {
                        "name": "Core",
                        "files": ["app/main.py"],
                        "description": "Create entrypoint",
                    }
                ],
            }
        )

    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fake_call_model,
    )

    result = await planner.create_plan("Build an API")

    assert result["name"] == "Demo App"
    assert result["file_tree"] == {"app/main.py": "service"}
    assert result["phases"][0]["files"] == ["app/main.py"]
    assert "estimated_credits" in result
    assert "estimated_time_minutes" in result


@pytest.mark.asyncio
async def test_project_planner_create_plan_accepts_mapping_model_output(monkeypatch) -> None:
    planner = ProjectPlanner()
    monkeypatch.setattr(planner, "_match_template", lambda _description: None)

    async def fake_call_model(provider, model, messages, **kwargs):
        assert provider == "stub"
        assert model == "stub"
        assert messages[-1]["role"] == "user"
        return {
            "name": "Demo App",
            "description": "Generated plan",
            "stack": {"backend": "FastAPI"},
            "file_tree": {"app/main.py": "service"},
            "phases": [
                {
                    "name": "Core",
                    "files": ["app/main.py"],
                    "description": "Create entrypoint",
                }
            ],
        }

    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fake_call_model,
    )

    result = await planner.create_plan("Build an API")

    assert result["name"] == "Demo App"
    assert result["file_tree"] == {"app/main.py": "service"}
    assert result["phases"][0]["files"] == ["app/main.py"]
    assert "estimated_credits" in result
    assert "estimated_time_minutes" in result
