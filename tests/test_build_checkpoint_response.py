from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import codey.saas.api.build_routes as build_routes


class _PhaseFilesResult:
    def __init__(self, files) -> None:
        self._files = files

    def scalars(self):
        return self

    def all(self):
        return self._files


class _CheckpointDB:
    def __init__(self, files) -> None:
        self._files = files
        self.added = []

    async def execute(self, *args, **kwargs):
        return _PhaseFilesResult(self._files)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


class _CheckpointStub:
    def __init__(self, **kwargs) -> None:
        self.id = uuid.uuid4()
        self.nfet_kappa = None
        self.nfet_sigma = None
        for key, value in kwargs.items():
            setattr(self, key, value)
        # Simulate a legacy DB-backed string value reaching the serializer.
        self.checkpoint_at = " 2026-01-02T03:04:05Z "


def test_checkpoint_to_response_coerces_malformed_fields() -> None:
    checkpoint = SimpleNamespace(
        id=uuid.uuid4(),
        phase="2",
        phase_name=["Phase 2"],
        files_in_phase={"count": 3},
        tests_passed="1",
        tests_failed=["0"],
        nfet_es_score="0.72",
        nfet_kappa={"value": 0.3},
        nfet_sigma="0.4",
        user_action=["continue"],
        checkpoint_at=" 2026-01-02T03:04:05Z ",
    )

    response = build_routes._checkpoint_to_response(checkpoint, "project-1")

    assert response.project_id == "project-1"
    assert response.phase == 2
    assert response.phase_name is None
    assert response.files_in_phase == 0
    assert response.tests_passed == 1
    assert response.tests_failed == 0
    assert response.nfet_es_score == 0.72
    assert response.nfet_kappa is None
    assert response.nfet_sigma == 0.4
    assert response.user_action is None
    assert response.checkpoint_at == "2026-01-02T03:04:05Z"


def test_build_numeric_coercion_rejects_non_finite_values() -> None:
    assert build_routes._coerce_build_int(float("nan"), fallback=-1) == -1
    assert build_routes._coerce_build_int(float("inf"), fallback=-1) == -1
    assert build_routes._coerce_build_int("-inf", fallback=-1) == -1
    assert build_routes._coerce_build_int("3", fallback=-1) == 3
    assert build_routes._coerce_optional_build_float(float("nan")) is None
    assert build_routes._coerce_optional_build_float("inf") is None
    assert build_routes._coerce_optional_build_float("0.72") == 0.72
    assert build_routes._coerce_estimated_credits(float("inf"), fallback=7) == 7


def test_build_row_list_coercion_rejects_malformed_results() -> None:
    row = SimpleNamespace(id="row-1")

    assert build_routes._coerce_build_row_list([row]) == [row]
    assert build_routes._coerce_build_row_list((row,)) == [row]
    assert build_routes._coerce_build_row_list(None) == []
    assert build_routes._coerce_build_row_list("bad") == []


@pytest.mark.asyncio
async def test_handle_checkpoint_tolerates_string_checkpoint_timestamp(monkeypatch) -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        nfet_es_score_final=0.72,
        total_phases=1,
        status="building",
        completed_at=None,
        current_phase=1,
    )
    db = _CheckpointDB([])

    async def fake_get_project(project_id, current_user, db_session):
        assert db_session is db
        return project

    monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
    monkeypatch.setattr(build_routes, "BuildCheckpoint", _CheckpointStub)

    response = await build_routes.handle_checkpoint(
        str(project.id),
        1,
        build_routes.CheckpointRequest(action="continue"),
        current_user=SimpleNamespace(id="user-1"),
        db=db,
    )

    assert response.checkpoint_at == "2026-01-02T03:04:05Z"
    assert response.project_id == str(project.id)
