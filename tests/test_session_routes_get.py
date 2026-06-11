from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

import codey.saas.api.session_routes as session_routes


@pytest.mark.asyncio
async def test_get_session_normalizes_legacy_string_files_uploaded(monkeypatch) -> None:
    started_at = datetime(2026, 1, 2, 3, 4, 5)

    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return SimpleNamespace(
            id="session-1",
            user_id="user-1",
            mode="analyze",
            prompt=None,
            files_uploaded=" demo.py ",
            repo_connected=None,
            status="completed",
            credits_charged=1,
            lines_generated=0,
            files_modified=0,
            nfet_phase_before=None,
            nfet_phase_after=None,
            es_score_before=None,
            es_score_after=None,
            output_summary=None,
            error_message=None,
            started_at=started_at,
            completed_at=None,
        )

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )

    response = await session_routes.get_session(
        "session-1",
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert response.files_uploaded == ["demo.py"]
    assert response.started_at == started_at.isoformat()


@pytest.mark.asyncio
async def test_get_session_tolerates_string_timestamps(monkeypatch) -> None:
    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return SimpleNamespace(
            id="session-1",
            user_id="user-1",
            mode="analyze",
            prompt=None,
            files_uploaded=None,
            repo_connected=None,
            status="completed",
            credits_charged=1,
            lines_generated=0,
            files_modified=0,
            nfet_phase_before=None,
            nfet_phase_after=None,
            es_score_before=None,
            es_score_after=None,
            output_summary=None,
            error_message=None,
            started_at=" 2026-01-02T03:04:05Z ",
            completed_at="2026-01-02T03:05:05Z",
        )

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )

    response = await session_routes.get_session(
        "session-1",
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert response.started_at == "2026-01-02T03:04:05Z"
    assert response.completed_at == "2026-01-02T03:05:05Z"


@pytest.mark.asyncio
async def test_get_session_normalizes_malformed_detail_fields(monkeypatch) -> None:
    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return SimpleNamespace(
            id="session-1",
            user_id="user-1",
            mode=["analyze"],
            prompt={"prompt": "fix bug"},
            files_uploaded=None,
            repo_connected={"repo": "owner/repo"},
            status=["completed"],
            credits_charged=" 3 ",
            lines_generated="12",
            files_modified=["1"],
            nfet_phase_before=["before"],
            nfet_phase_after={"after": "done"},
            es_score_before="0.75",
            es_score_after={"score": 0.9},
            output_summary=["summary"],
            error_message={"error": "boom"},
            started_at="2026-01-02T03:04:05Z",
            completed_at=None,
        )

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )

    response = await session_routes.get_session(
        "session-1",
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert response.mode == "unknown"
    assert response.prompt is None
    assert response.repo_connected is None
    assert response.status == "unknown"
    assert response.credits_charged == 3
    assert response.lines_generated == 12
    assert response.files_modified == 0
    assert response.nfet_phase_before is None
    assert response.nfet_phase_after is None
    assert response.es_score_before == 0.75
    assert response.es_score_after is None
    assert response.output_summary is None
    assert response.error_message is None


@pytest.mark.asyncio
async def test_get_session_tolerates_missing_optional_detail_fields(monkeypatch) -> None:
    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return SimpleNamespace(
            id="session-1",
            user_id="user-1",
        )

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )

    response = await session_routes.get_session(
        "session-1",
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert response.id == "session-1"
    assert response.user_id == "user-1"
    assert response.mode == "unknown"
    assert response.prompt is None
    assert response.files_uploaded is None
    assert response.repo_connected is None
    assert response.status == "unknown"
    assert response.credits_charged == 0
    assert response.lines_generated == 0
    assert response.files_modified == 0
    assert response.nfet_phase_before is None
    assert response.nfet_phase_after is None
    assert response.es_score_before is None
    assert response.es_score_after is None
    assert response.output_summary is None
    assert response.error_message is None
    assert response.started_at == ""
    assert response.completed_at is None


def test_session_numeric_coercion_rejects_non_finite_values() -> None:
    assert session_routes._coerce_session_int(True, fallback=-1) == -1
    assert session_routes._coerce_session_int(float("nan"), fallback=-1) == -1
    assert session_routes._coerce_session_int(float("inf"), fallback=-1) == -1
    assert session_routes._coerce_session_int("3", fallback=-1) == 3
    assert session_routes._coerce_session_float(True) is None
    assert session_routes._coerce_session_float(float("nan")) is None
    assert session_routes._coerce_session_float("inf") is None
    assert session_routes._coerce_session_float("0.75") == 0.75
