from __future__ import annotations

import json
import uuid

import pytest

import codey.saas.api.build_routes as build_routes
from codey.saas.models import BuildFile, BuildProject


class _CurrentUser:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeDB:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            setattr(obj, "id", uuid.uuid4())
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid.uuid4())

    @property
    def project(self) -> BuildProject:
        return next(obj for obj in self.added if isinstance(obj, BuildProject))

    @property
    def build_files(self) -> list[BuildFile]:
        return [obj for obj in self.added if isinstance(obj, BuildFile)]


def test_build_templates_route_precedes_project_id_route() -> None:
    get_paths = [
        route.path
        for route in build_routes.router.routes
        if "GET" in getattr(route, "methods", set())
    ]

    assert get_paths.index("/build/templates") < get_paths.index("/build/{project_id}")


@pytest.mark.asyncio
async def test_build_plan_normalizes_malformed_llm_phase_shapes(monkeypatch) -> None:
    db = _FakeDB()
    user = _CurrentUser()

    async def fake_call_model(*args, **kwargs) -> str:
        return json.dumps(
            {
                "phases": [
                    None,
                    {"name": "Backend", "files": "app/main.py"},
                    {"name": ["API"], "files": ["app/routes.py", None, 5, "  "]},
                    {"name": "Docs", "files": {"path": "README.md"}},
                    "skip-me",
                ],
                "estimated_credits": "19",
            }
        )

    monkeypatch.setattr(
        build_routes,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(build_routes, "call_model", fake_call_model)

    response = await build_routes.build_plan(
        build_routes.BuildPlanRequest(description="Build a demo API"),
        current_user=user,
        db=db,
    )

    assert [phase.name for phase in response.phases] == ["Backend", "Phase 2", "Docs"]
    assert [phase.files for phase in response.phases] == [
        ["app/main.py"],
        ["app/routes.py"],
        [],
    ]
    assert response.total_files == 2
    assert response.estimated_credits == 19
    assert [build_file.file_path for build_file in db.build_files] == [
        "app/main.py",
        "app/routes.py",
    ]
    assert "a" not in [build_file.file_path for build_file in db.build_files]


@pytest.mark.asyncio
async def test_build_plan_defaults_answers_and_falls_back_for_non_object_payload(monkeypatch) -> None:
    db = _FakeDB()
    user = _CurrentUser()
    body = build_routes.BuildPlanRequest(description="Build a small tool")
    body.answers = "broken-shape"

    async def fake_call_model(*args, **kwargs) -> str:
        return json.dumps(["not", "a", "mapping"])

    monkeypatch.setattr(
        build_routes,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(build_routes, "call_model", fake_call_model)

    response = await build_routes.build_plan(
        body,
        current_user=user,
        db=db,
    )

    assert response.stack == {
        "language": "Python",
        "framework": "auto",
        "database": "PostgreSQL",
        "testing": True,
        "deployment": "Docker",
    }
    assert response.total_files > 0
    assert response.phases[0].files == [
        "requirements.txt",
        "pyproject.toml",
        ".env.example",
        "Dockerfile",
    ]
    assert db.project.stack == response.stack


@pytest.mark.asyncio
async def test_build_plan_accepts_mapping_llm_output(monkeypatch) -> None:
    db = _FakeDB()
    user = _CurrentUser()

    async def fake_call_model(*args, **kwargs):
        return {
            "phases": [
                {"name": "Setup", "files": ["requirements.txt", "app/main.py"]},
                {"name": "Tests", "files": ["tests/test_main.py"]},
            ],
            "estimated_credits": 27,
        }

    monkeypatch.setattr(
        build_routes,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(build_routes, "call_model", fake_call_model)

    response = await build_routes.build_plan(
        build_routes.BuildPlanRequest(description="Build a demo API"),
        current_user=user,
        db=db,
    )

    assert [phase.name for phase in response.phases] == ["Setup", "Tests"]
    assert [phase.files for phase in response.phases] == [
        ["requirements.txt", "app/main.py"],
        ["tests/test_main.py"],
    ]
    assert response.total_files == 3
    assert response.estimated_credits == 27
    assert [build_file.file_path for build_file in db.build_files] == [
        "requirements.txt",
        "app/main.py",
        "tests/test_main.py",
    ]


@pytest.mark.asyncio
async def test_build_plan_filters_unsafe_llm_file_paths(monkeypatch) -> None:
    db = _FakeDB()
    user = _CurrentUser()

    async def fake_call_model(*args, **kwargs):
        return {
            "phases": [
                {
                    "name": "Setup",
                    "files": [
                        "app/main.py",
                        "../secrets.py",
                        "/etc/passwd",
                        "C:\\temp\\secret.py",
                        "./src\\utils.py",
                        "app/main.py",
                    ],
                },
                {
                    "name": "Workers",
                    "files": ["./app//main.py", "src\\worker.py"],
                },
            ],
            "estimated_credits": 27,
        }

    monkeypatch.setattr(
        build_routes,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(build_routes, "call_model", fake_call_model)

    response = await build_routes.build_plan(
        build_routes.BuildPlanRequest(description="Build a demo API"),
        current_user=user,
        db=db,
    )

    assert [phase.files for phase in response.phases] == [
        ["app/main.py", "src/utils.py"],
        ["src/worker.py"],
    ]
    assert [build_file.file_path for build_file in db.build_files] == [
        "app/main.py",
        "src/utils.py",
        "src/worker.py",
    ]
