from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

import codey.saas.api.user_routes as user_routes


class _CountResult:
    def __init__(self, total: int) -> None:
        self._total = total

    def scalar_one(self) -> int:
        return self._total


class _SessionsResult:
    def __init__(self, sessions) -> None:
        self._sessions = sessions

    def scalars(self):
        return self

    def all(self):
        return self._sessions


class _UserSessionsDB:
    def __init__(self, total: int, sessions) -> None:
        self._results = [_CountResult(total), _SessionsResult(sessions)]

    async def execute(self, _statement):
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_get_my_sessions_uses_query_defaults_when_called_directly() -> None:
    session = SimpleNamespace(
        id="session-1",
        mode="autonomous",
        prompt="Fix deployment",
        repo_connected="octo/repo",
        status="completed",
        credits_charged=5,
        lines_generated=42,
        files_modified=3,
        nfet_phase_before="build",
        nfet_phase_after="validate",
        es_score_before=0.1,
        es_score_after=0.2,
        output_summary="Completed successfully",
        error_message=None,
        started_at=datetime(2024, 1, 1, 12, 0, 0),
        completed_at=None,
    )

    response = await user_routes.get_my_sessions(
        current_user=SimpleNamespace(id="user-1"),
        db=_UserSessionsDB(1, [session]),
    )

    assert response.total == 1
    assert response.limit == 20
    assert response.offset == 0
    assert len(response.sessions) == 1
