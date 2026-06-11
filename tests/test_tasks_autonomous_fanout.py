from __future__ import annotations

import logging
import uuid

import codey.saas.tasks.autonomous as autonomous


def test_coerce_stress_threshold_rejects_out_of_range_values() -> None:
    assert autonomous._coerce_stress_threshold("-0.1") == 0.7
    assert autonomous._coerce_stress_threshold("1.1") == 0.7
    assert autonomous._coerce_stress_threshold("nan") == 0.7
    assert autonomous._coerce_stress_threshold("0.45") == 0.45


def test_coerce_autonomous_dispatch_limit_bounds_values() -> None:
    assert autonomous._coerce_autonomous_dispatch_limit("3") == 3
    assert autonomous._coerce_autonomous_dispatch_limit("0") == 1
    assert autonomous._coerce_autonomous_dispatch_limit("1001") == 1000
    assert autonomous._coerce_autonomous_dispatch_limit("nan") == 100
    assert autonomous._coerce_autonomous_dispatch_limit(True) == 100


def test_coerce_autonomous_identifier_rejects_ascii_controls() -> None:
    assert autonomous._coerce_autonomous_identifier(" repo-1 ") == "repo-1"
    assert autonomous._coerce_autonomous_identifier("repo-1\nbad") is None
    assert autonomous._coerce_autonomous_identifier("repo-1\x7fbad") is None


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self) -> _FakeMappingsResult:
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, _statement, _params=None):
        return _FakeMappingsResult(self._rows)


class _RecordingSession(_FakeSession):
    def __init__(self, rows):
        super().__init__(rows)
        self.executed = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        return await super().execute(statement, params)


class _FailingSession(_FakeSession):
    async def execute(self, _statement, _params=None):
        raise RuntimeError(
            "database unavailable https://user:secret@example.test/db"
        )


def test_run_all_autonomous_repos_uses_dispatch_limit(monkeypatch) -> None:
    session = _RecordingSession([{"id": "repo-1", "user_id": "user-1"}])
    dispatched_calls: list[tuple[list[str], str]] = []

    monkeypatch.setenv("CODEY_AUTONOMOUS_DISPATCH_LIMIT", "7")
    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )

    def fake_apply_async(*, args, queue):
        dispatched_calls.append((args, queue))

    monkeypatch.setattr(autonomous.run_autonomous_repo, "apply_async", fake_apply_async)

    result = autonomous.run_all_autonomous_repos.run()

    assert dispatched_calls == [
        (["repo-1", "user-1"], "autonomous"),
    ]
    assert result == {"dispatched": 1}
    statement, params = session.executed[0]
    assert "ORDER BY last_analyzed ASC NULLS FIRST" in statement
    assert "LIMIT :limit" in statement
    assert params == {"limit": 7}


def test_run_all_autonomous_repos_returns_failed_on_repo_query_error(
    monkeypatch,
    caplog,
) -> None:
    dispatched_calls: list[tuple[list[str], str]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: _FailingSession([]),
    )

    def fake_apply_async(*, args, queue):
        dispatched_calls.append((args, queue))

    monkeypatch.setattr(autonomous.run_autonomous_repo, "apply_async", fake_apply_async)
    caplog.set_level(logging.WARNING, logger="codey.saas.tasks.autonomous")

    result = autonomous.run_all_autonomous_repos.run()

    assert dispatched_calls == []
    assert result == {
        "status": "failed",
        "reason": "repo_query_failed",
        "dispatched": 0,
    }
    assert "secret" not in caplog.text
    assert "https://***@example.test/db" in caplog.text
    assert "Traceback" not in caplog.text


def test_run_all_autonomous_repos_continues_after_enqueue_failure(monkeypatch) -> None:
    rows = [
        {"id": "repo-1", "user_id": "user-1"},
        {"id": "repo-2", "user_id": "user-2"},
    ]
    dispatched_calls: list[tuple[list[str], str]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: _FakeSession(rows),
    )

    def fake_apply_async(*, args, queue):
        dispatched_calls.append((args, queue))
        if len(dispatched_calls) == 1:
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr(autonomous.run_autonomous_repo, "apply_async", fake_apply_async)

    result = autonomous.run_all_autonomous_repos.run()

    assert dispatched_calls == [
        (["repo-1", "user-1"], "autonomous"),
        (["repo-2", "user-2"], "autonomous"),
    ]
    assert result == {"dispatched": 1}


def test_run_all_autonomous_repos_redacts_enqueue_failure_repo_id(
    monkeypatch,
    caplog,
) -> None:
    rows = [
        {
            "id": "https://user:repo-secret@example.test/repo.git?token=repo-token",
            "user_id": "user-1",
        },
    ]

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: _FakeSession(rows),
    )

    def fake_apply_async(*, args, queue):
        raise RuntimeError(
            "queue unavailable redis://user:queue-secret@redis.example/0"
            "?password=queue-password"
        )

    monkeypatch.setattr(autonomous.run_autonomous_repo, "apply_async", fake_apply_async)
    caplog.set_level(logging.WARNING, logger="codey.saas.tasks.autonomous")

    result = autonomous.run_all_autonomous_repos.run()

    assert result == {"dispatched": 0}
    assert "repo-secret" not in caplog.text
    assert "repo-token" not in caplog.text
    assert "queue-secret" not in caplog.text
    assert "queue-password" not in caplog.text
    assert "https://***@example.test/repo.git?token=***" in caplog.text
    assert "redis://***@redis.example/0?password=***" in caplog.text


def test_run_all_autonomous_repos_redacts_malformed_rows(monkeypatch, caplog) -> None:
    rows = [
        {
            "id": {
                "repo": (
                    "https://user:row-secret@example.test/repo.git"
                    "?access_token=row-token"
                ),
            },
            "user_id": None,
        },
    ]
    dispatched_calls: list[tuple[list[str], str]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: _FakeSession(rows),
    )

    def fake_apply_async(*, args, queue):
        dispatched_calls.append((args, queue))

    monkeypatch.setattr(autonomous.run_autonomous_repo, "apply_async", fake_apply_async)
    caplog.set_level(logging.WARNING, logger="codey.saas.tasks.autonomous")

    result = autonomous.run_all_autonomous_repos.run()

    assert result == {"dispatched": 0}
    assert dispatched_calls == []
    assert "row-secret" not in caplog.text
    assert "row-token" not in caplog.text
    assert "https://***@example.test/repo.git?access_token=***" in caplog.text


def test_run_all_autonomous_repos_skips_malformed_repo_rows(monkeypatch) -> None:
    rows = [
        "bad-row",
        {"id": {"repo": "bad"}, "user_id": None},
        {"id": "repo-1\nbad", "user_id": "user-1"},
        {"id": "repo-2", "user_id": "user-2"},
    ]
    dispatched_calls: list[tuple[list[str], str]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: _FakeSession(rows),
    )

    def fake_apply_async(*, args, queue):
        dispatched_calls.append((args, queue))

    monkeypatch.setattr(autonomous.run_autonomous_repo, "apply_async", fake_apply_async)

    result = autonomous.run_all_autonomous_repos.run()

    assert dispatched_calls == [
        (["repo-2", "user-2"], "autonomous"),
    ]
    assert result == {"dispatched": 1}


def test_run_all_autonomous_repos_dispatches_uuid_identifiers(monkeypatch) -> None:
    repo_id = uuid.uuid4()
    user_id = uuid.uuid4()
    rows = [{"id": repo_id, "user_id": user_id}]
    dispatched_calls: list[tuple[list[str], str]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: _FakeSession(rows),
    )

    def fake_apply_async(*, args, queue):
        dispatched_calls.append((args, queue))

    monkeypatch.setattr(autonomous.run_autonomous_repo, "apply_async", fake_apply_async)

    result = autonomous.run_all_autonomous_repos.run()

    assert dispatched_calls == [
        ([str(repo_id), str(user_id)], "autonomous"),
    ]
    assert result == {"dispatched": 1}
