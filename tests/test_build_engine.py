from __future__ import annotations

import uuid
import zipfile
from pathlib import Path

import pytest

from codey.saas.build_mode.engine import (
    BuildEngine,
    _coerce_build_plan,
    _coerce_estimated_credits,
    _redact_build_error,
)
from codey.saas.build_mode.generator import BuildContext


class _FakeDB:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


def test_get_phase_info_ignores_non_mapping_phase_entries() -> None:
    engine = BuildEngine(db=object(), user_id=uuid.uuid4())

    phase_info = engine._get_phase_info(
        {"phases": ["skip-me", {"name": "Core", "description": "Build core"}]},
        0,
    )

    assert phase_info == {"name": "Core", "description": "Build core"}


def test_redact_build_error_removes_common_secret_shapes() -> None:
    error = _redact_build_error(
        RuntimeError(
            "failed for https://user:secret@example.com/owner/repo.git?access_token=access123&client_secret=client123 "
            "api_key=key123 auth_token=auth123 refresh_token=refresh123 "
            "password=pw123 operator@example.com authorization=Bearer bearer123"
        )
    )

    assert "user:secret" not in error
    assert "secret@example.com" not in error
    assert "access123" not in error
    assert "client123" not in error
    assert "key123" not in error
    assert "auth123" not in error
    assert "refresh123" not in error
    assert "pw123" not in error
    assert "bearer123" not in error
    assert "operator@example.com" not in error
    assert "https://***@example.com/owner/repo.git?access_token=***&client_secret=***" in error
    assert "api_key=***" in error
    assert "auth_token=***" in error
    assert "refresh_token=***" in error
    assert "password=***" in error
    assert "authorization=Bearer ***" in error
    assert "***@example.com" in error


def test_coerce_estimated_credits_rejects_malformed_bounds() -> None:
    assert _coerce_estimated_credits({"min": True, "max": float("inf")}) == {
        "min": 20,
        "max": 20,
    }
    assert _coerce_estimated_credits({"min": float("nan"), "max": 5}) == {
        "min": 20,
        "max": 20,
    }
    assert _coerce_estimated_credits({"min": 2, "max": 1}) == {
        "min": 2,
        "max": 2,
    }


def test_coerce_build_plan_rejects_unsafe_file_paths() -> None:
    plan = _coerce_build_plan(
        {
            "name": "Demo",
            "file_tree": {
                "/tmp/secret.py": "service",
                "../secret.py": "service",
                "C:\\tmp\\secret.py": "service",
                "bad\x00name.py": "service",
                "bad\nname.py": "service",
                "bad\tname.py": "service",
                "bad\x7fname.py": "service",
                "app/main.py:ads": "service",
                "src:bad/main.py": "service",
                "./app//main.py": "service",
                "app\\models\\.\\user.py": "model",
                "app/../escape.py": "service",
            },
            "phases": [
                {
                    "name": "Core",
                    "files": [
                        "../secret.py",
                        "/tmp/secret.py",
                        "C:/tmp/secret.py",
                        "bad\nname.py",
                        "bad\tname.py",
                        "bad\x7fname.py",
                        "app/main.py:ads",
                        "src:bad/main.py",
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
async def test_package_project_falls_back_for_non_string_plan_name(tmp_path: Path) -> None:
    engine = BuildEngine(db=object(), user_id=uuid.uuid4())
    context = BuildContext(
        project_plan={"name": ["broken-shape"]},
        generated_files={"app/main.py": "print('ok')\n"},
    )

    archive_path = await engine._package_project(uuid.uuid4(), context)

    assert Path(archive_path).name.startswith("project_")
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["project/app/main.py"]


@pytest.mark.asyncio
async def test_package_project_deduplicates_colliding_archive_paths() -> None:
    engine = BuildEngine(db=object(), user_id=uuid.uuid4())
    context = BuildContext(
        project_plan={"name": "Demo"},
        generated_files={
            "app//main.py": "print('one')\n",
            "app/main.py": "print('two')\n",
        },
    )

    archive_path = await engine._package_project(uuid.uuid4(), context)

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == [
            "demo/app/main.py",
            "demo/app/main-2.py",
        ]
        assert archive.read("demo/app/main.py") == b"print('one')\n"
        assert archive.read("demo/app/main-2.py") == b"print('two')\n"


@pytest.mark.asyncio
async def test_package_project_tolerates_mixed_generated_file_shapes() -> None:
    engine = BuildEngine(db=object(), user_id=uuid.uuid4())
    context = BuildContext(
        project_plan={"name": "Demo"},
        generated_files={  # type: ignore[dict-item]
            2: 42,
            "app/main.py": "print('one')\n",
        },
    )

    archive_path = await engine._package_project(uuid.uuid4(), context)

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == [
            "demo/2",
            "demo/app/main.py",
        ]
        assert archive.read("demo/2") == b"42"
        assert archive.read("demo/app/main.py") == b"print('one')\n"


@pytest.mark.asyncio
async def test_package_project_rejects_empty_generated_files() -> None:
    engine = BuildEngine(db=object(), user_id=uuid.uuid4())
    context = BuildContext(project_plan={"name": "Demo"}, generated_files={})

    with pytest.raises(ValueError, match="No generated files"):
        await engine._package_project(uuid.uuid4(), context)


@pytest.mark.asyncio
async def test_start_build_normalizes_scalar_estimated_credits(monkeypatch) -> None:
    db = _FakeDB()
    engine = BuildEngine(db=db, user_id=uuid.uuid4())

    async def fake_clarify(_description: str) -> dict[str, object]:
        return {"questions": [], "defaults": {}, "template_match": None}

    async def fake_create_plan(_description: str) -> dict[str, object]:
        return {
            "name": "Demo App",
            "description": "Generated plan",
            "estimated_credits": "broken-shape",
            "phases": [],
            "file_tree": {},
            "stack": {},
        }

    async def fake_check_credits(_user_id, required: int) -> bool:
        assert required == 20
        return True

    monkeypatch.setattr(engine.planner, "clarify", fake_clarify)
    monkeypatch.setattr(engine.planner, "create_plan", fake_create_plan)
    monkeypatch.setattr(engine.credit_service, "check_credits", fake_check_credits)

    result = await engine.start_build("Build a demo app")

    assert result["estimated_credits"] == {"min": 20, "max": 20}
    assert result["plan"]["estimated_credits"] == {"min": 20, "max": 20}
    assert db.added


@pytest.mark.asyncio
async def test_start_build_tolerates_malformed_clarification_and_plan(
    monkeypatch,
) -> None:
    db = _FakeDB()
    engine = BuildEngine(db=db, user_id=uuid.uuid4())

    async def fake_clarify(_description: str) -> dict[str, object]:
        return {"questions": "not-a-list", "defaults": ["bad-shape"]}

    async def fake_create_plan(_description: str) -> dict[str, object]:
        return {
            "name": ["bad-name"],
            "description": b" Generated plan ",
            "estimated_credits": {"min": "7", "max": "3"},
            "phases": ["bad-phase", {"name": "Core"}],
            "file_tree": ["bad-tree"],
            "stack": "bad-stack",
        }

    async def fake_check_credits(_user_id, required: int) -> bool:
        assert required == 7
        return True

    monkeypatch.setattr(engine.planner, "clarify", fake_clarify)
    monkeypatch.setattr(engine.planner, "create_plan", fake_create_plan)
    monkeypatch.setattr(engine.credit_service, "check_credits", fake_check_credits)

    result = await engine.start_build("Build a demo app")

    assert result["status"] == "plan_ready"
    assert result["plan"] == {
        "name": "Untitled",
        "description": "Generated plan",
        "estimated_credits": {"min": 7, "max": 7},
        "phases": [{"name": "Core"}],
        "file_tree": {},
        "stack": {},
    }
    assert db.added[0].name == "Untitled"
    assert db.added[0].total_phases == 1
    assert db.added[0].files_planned == 0


@pytest.mark.asyncio
async def test_start_build_with_answers_tolerates_non_mapping_plan(
    monkeypatch,
) -> None:
    db = _FakeDB()
    engine = BuildEngine(db=db, user_id=uuid.uuid4())

    async def fake_create_plan(
        _description: str,
        answers: dict[str, str],
    ) -> list[str]:
        assert answers == {"database": "PostgreSQL"}
        return ["bad-plan"]

    async def fake_check_credits(_user_id, required: int) -> bool:
        assert required == 20
        return True

    monkeypatch.setattr(engine.planner, "create_plan", fake_create_plan)
    monkeypatch.setattr(engine.credit_service, "check_credits", fake_check_credits)

    result = await engine.start_build_with_answers(
        "Build a demo app",
        {"database": "PostgreSQL"},
    )

    assert result["status"] == "plan_ready"
    assert result["plan"] == {
        "name": "Untitled",
        "description": "",
        "stack": {},
        "file_tree": {},
        "phases": [],
        "estimated_credits": {"min": 20, "max": 20},
    }
    assert db.added[0].name == "Untitled"
