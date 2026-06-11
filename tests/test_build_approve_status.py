from __future__ import annotations

from datetime import datetime
import uuid

import pytest

import codey.saas.api.build_routes as build_routes
from codey.saas.models import BuildFile


class _CurrentUser:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _ProjectStub:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.status = "planning"
        self.files_planned = 0
        self.name = "demo"
        self.description = "demo project"
        self.credits_charged = 0
        self.current_phase = 0
        self.session_id = None
        self.files_completed = 0
        self.lines_generated = 0
        self.completed_at = None


class _EmptyBuildFilesResult:
    def __init__(self, files=None) -> None:
        self._files = files or []

    def scalars(self):
        return self

    def all(self):
        return self._files


class _FakeDB:
    async def execute(self, *args, **kwargs):
        return _EmptyBuildFilesResult()

    async def flush(self) -> None:
        return None


class _FailingBuildDB(_FakeDB):
    async def execute(self, *args, **kwargs):
        raise RuntimeError("query failed")


class _BuildFilesDB(_FakeDB):
    def __init__(self, files) -> None:
        self._files = files

    async def execute(self, *args, **kwargs):
        return _EmptyBuildFilesResult(self._files)


@pytest.mark.asyncio
async def test_build_approve_returns_actual_project_status(monkeypatch) -> None:
    project = _ProjectStub()
    db = _FakeDB()
    user = _CurrentUser()

    async def fake_get_project(project_id, current_user, db):
        return project

    async def fake_reserve_credits(self, **kwargs) -> None:
        return None

    monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
    monkeypatch.setattr(
        build_routes.CreditService,
        "reserve_credits",
        fake_reserve_credits,
    )
    monkeypatch.setattr(
        build_routes,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )

    response = await build_routes.build_approve(
        str(project.id),
        current_user=user,
        db=db,
    )

    assert project.status == "completed"
    assert isinstance(project.completed_at, datetime)
    assert response.status == "completed"


@pytest.mark.asyncio
async def test_build_approve_coerces_malformed_files_planned(monkeypatch) -> None:
    project = _ProjectStub()
    project.files_planned = {"not": "a count"}
    db = _FakeDB()
    user = _CurrentUser()
    reserved_costs: list[int] = []

    async def fake_get_project(project_id, current_user, db):
        return project

    async def fake_reserve_credits(self, **kwargs) -> None:
        reserved_costs.append(kwargs["estimated_cost"])

    monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
    monkeypatch.setattr(
        build_routes.CreditService,
        "reserve_credits",
        fake_reserve_credits,
    )
    monkeypatch.setattr(build_routes, "resolve_model", lambda *_args, **_kwargs: ("stub", "stub"))

    response = await build_routes.build_approve(
        str(project.id),
        current_user=user,
        db=db,
    )

    expected_cost = max(build_routes.CREDIT_COSTS["full_build"], 12 * 2)
    assert reserved_costs == [expected_cost]
    assert project.credits_charged == expected_cost
    assert response.status == "completed"


@pytest.mark.asyncio
async def test_build_approve_marks_project_failed_on_fatal_generation_error(monkeypatch) -> None:
    project = _ProjectStub()
    db = _FailingBuildDB()
    user = _CurrentUser()
    refunds: list[tuple[uuid.UUID, int, str, uuid.UUID | None]] = []

    async def fake_get_project(project_id, current_user, db):
        return project

    async def fake_reserve_credits(self, **kwargs) -> None:
        return None

    async def fake_refund_credits(
        self,
        *,
        user_id,
        amount,
        description,
        session_id=None,
    ) -> None:
        refunds.append((user_id, amount, description, session_id))

    monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
    monkeypatch.setattr(
        build_routes.CreditService,
        "reserve_credits",
        fake_reserve_credits,
    )
    monkeypatch.setattr(
        build_routes.CreditService,
        "refund_credits",
        fake_refund_credits,
    )

    response = await build_routes.build_approve(
        str(project.id),
        current_user=user,
        db=db,
    )

    assert project.status == "failed"
    assert project.credits_charged == 0
    assert refunds == [
        (
            user.id,
            max(build_routes.CREDIT_COSTS["full_build"], (project.files_planned or 12) * 2),
            "Refund failed build project: demo",
            None,
        )
    ]
    assert response.status == "failed"


@pytest.mark.asyncio
async def test_build_approve_marks_project_failed_when_any_file_generation_fails(monkeypatch) -> None:
    project = _ProjectStub()
    build_file = BuildFile(project_id=project.id, file_path="app/main.py", phase=1)
    db = _BuildFilesDB([build_file])
    user = _CurrentUser()

    async def fake_get_project(project_id, current_user, db):
        return project

    async def fake_reserve_credits(self, **kwargs) -> None:
        return None

    async def fake_call_model(*args, **kwargs) -> str:
        raise RuntimeError(
            "generation failed for https://user:pass@example.com/repo?"
            "client_secret=super-secret&token=raw-token "
            "authorization=Bearer ghp_raw user@example.com"
        )

    monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
    monkeypatch.setattr(
        build_routes.CreditService,
        "reserve_credits",
        fake_reserve_credits,
    )
    monkeypatch.setattr(build_routes, "resolve_model", lambda *_args, **_kwargs: ("stub", "stub"))
    monkeypatch.setattr(build_routes, "call_model", fake_call_model)

    response = await build_routes.build_approve(
        str(project.id),
        current_user=user,
        db=db,
    )

    assert build_file.status == "failed"
    assert build_file.validation_passed is False
    assert build_file.content.startswith("# Generation failed:")
    assert "super-secret" not in build_file.content
    assert "raw-token" not in build_file.content
    assert "ghp_raw" not in build_file.content
    assert "user:pass" not in build_file.content
    assert "user@example.com" not in build_file.content
    assert "client_secret=***" in build_file.content
    assert "token=***" in build_file.content
    assert "authorization=Bearer ***" in build_file.content
    assert "https://***@example.com/repo" in build_file.content
    assert "***@example.com" in build_file.content
    assert project.status == "failed"
    assert response.status == "failed"


@pytest.mark.asyncio
async def test_build_approve_rejects_unsafe_file_paths_without_generation(monkeypatch) -> None:
    project = _ProjectStub()
    build_file = BuildFile(project_id=project.id, file_path="../secrets.py", phase=1)
    db = _BuildFilesDB([build_file])
    user = _CurrentUser()

    async def fake_get_project(project_id, current_user, db):
        return project

    async def fake_reserve_credits(self, **kwargs) -> None:
        return None

    async def fail_call_model(*args, **kwargs) -> str:
        raise AssertionError("generation should not run for unsafe file paths")

    monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
    monkeypatch.setattr(
        build_routes.CreditService,
        "reserve_credits",
        fake_reserve_credits,
    )
    monkeypatch.setattr(build_routes, "resolve_model", lambda *_args, **_kwargs: ("stub", "stub"))
    monkeypatch.setattr(build_routes, "call_model", fail_call_model)

    response = await build_routes.build_approve(
        str(project.id),
        current_user=user,
        db=db,
    )

    assert build_file.status == "failed"
    assert build_file.validation_passed is False
    assert "Invalid build file path" in build_file.content
    assert project.status == "failed"
    assert response.status == "failed"


@pytest.mark.asyncio
async def test_build_approve_accepts_mapping_model_output(monkeypatch) -> None:
    project = _ProjectStub()
    build_file = BuildFile(project_id=project.id, file_path="app/main.py", phase=1)
    db = _BuildFilesDB([build_file])
    user = _CurrentUser()

    async def fake_get_project(project_id, current_user, db):
        return project

    async def fake_reserve_credits(self, **kwargs) -> None:
        return None

    async def fake_call_model(*args, **kwargs):
        return {"content": "print('ok')"}

    monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
    monkeypatch.setattr(
        build_routes.CreditService,
        "reserve_credits",
        fake_reserve_credits,
    )
    monkeypatch.setattr(build_routes, "resolve_model", lambda *_args, **_kwargs: ("stub", "stub"))
    monkeypatch.setattr(build_routes, "call_model", fake_call_model)

    response = await build_routes.build_approve(
        str(project.id),
        current_user=user,
        db=db,
    )

    assert build_file.status == "completed"
    assert build_file.validation_passed is True
    assert build_file.content == "print('ok')"
    assert build_file.line_count == 1
    assert project.status == "completed"
    assert response.status == "completed"
