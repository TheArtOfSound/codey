from __future__ import annotations

import logging
import uuid

import codey.saas.tasks.builds as builds


class _FakeMappingsResult:
    def __init__(self, *, first_row=None, all_rows=None):
        self._first_row = first_row
        self._all_rows = all_rows or []

    def mappings(self) -> _FakeMappingsResult:
        return self

    def first(self):
        return self._first_row

    def all(self):
        return self._all_rows


class _FakeSession:
    def __init__(self, project_row, *, file_rows=None):
        self._project_row = project_row
        self._file_rows = file_rows or []
        self.executed: list[tuple[str, dict | None]] = []
        self.commits = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append((sql, params))
        if "FROM build_projects" in sql:
            return _FakeMappingsResult(first_row=self._project_row)
        if "FROM build_files" in sql:
            return _FakeMappingsResult(all_rows=self._file_rows)
        return _FakeMappingsResult()

    async def commit(self) -> None:
        self.commits += 1


def test_coerce_positive_int_rejects_malformed_values() -> None:
    assert builds._coerce_positive_int(True, default=9) == 9
    assert builds._coerce_positive_int(float("nan"), default=9) == 9
    assert builds._coerce_positive_int(float("inf"), default=9) == 9
    assert builds._coerce_positive_int("-1", default=9) == 9
    assert builds._coerce_positive_int("3", default=9) == 3
    assert builds._coerce_positive_int(10**10000, default=9) == 10_000


def test_coerce_nonnegative_int_rejects_malformed_values() -> None:
    assert builds._coerce_nonnegative_int(True, default=9) == 9
    assert builds._coerce_nonnegative_int(float("nan"), default=9) == 9
    assert builds._coerce_nonnegative_int(float("inf"), default=9) == 9
    assert builds._coerce_nonnegative_int("-1", default=9) == 9
    assert builds._coerce_nonnegative_int("0", default=9) == 0
    assert builds._coerce_nonnegative_int("3", default=9) == 3
    assert builds._coerce_nonnegative_int(10**10000, default=9) == 10_000


def test_coerce_generated_file_content_accepts_structured_text_blocks() -> None:
    assert (
        builds._coerce_generated_file_content(
            {
                "content": [
                    {"type": "text", "text": "print('one')"},
                    {"type": "image", "source": "ignored"},
                    {"type": "text", "text": "print('two')"},
                ]
            }
        )
        == "print('one')\nprint('two')"
    )
    assert (
        builds._coerce_generated_file_content(
            {"content": {"type": "image", "source": "ignored"}, "code": "print('ok')"}
        )
        == "print('ok')"
    )


def test_parse_generated_file_content_extracts_fenced_blocks() -> None:
    assert (
        builds._parse_generated_file_content(
            '```python title="app/main.py"\r\nprint("ok")\r\n```'
        )
        == 'print("ok")'
    )
    assert (
        builds._parse_generated_file_content(
            {"content": '```typescript jsx\nexport default function App() { return null }\n```'}
        )
        == "export default function App() { return null }"
    )
    assert builds._parse_generated_file_content("print('raw')\n") == "print('raw')\n"


def test_parse_generated_file_content_rejects_empty_output() -> None:
    for value in (" \n\t", "```python\n \n```"):
        try:
            builds._parse_generated_file_content(value)
        except TypeError as exc:
            assert "empty generated file content" in str(exc)
        else:
            raise AssertionError("empty generated content should be rejected")


def test_count_generated_file_lines_ignores_blank_and_trailing_lines() -> None:
    assert builds._count_generated_file_lines("") == 0
    assert builds._count_generated_file_lines("print('ok')\n") == 1
    assert (
        builds._count_generated_file_lines("\nprint('one')\n\nprint('two')\n")
        == 2
    )


def test_coerce_task_identifier_rejects_malformed_text() -> None:
    assert builds._coerce_task_identifier(" file-1 ") == "file-1"
    assert builds._coerce_task_identifier("file-1 bad") is None
    assert builds._coerce_task_identifier("file-1\nbad") is None
    assert builds._coerce_task_identifier("file-1\x7fbad") is None


def test_redact_task_error_hides_common_secret_shapes() -> None:
    message = builds._redact_task_error(
        "provider failed https://user:url-secret@example.test/model"
        "?api_key=query-secret authorization=Bearer bearer-secret "
        "mirror=https://example.test/model#access_token=fragment-secret "
        "for operator@example.test",
    )

    assert "url-secret" not in message
    assert "query-secret" not in message
    assert "fragment-secret" not in message
    assert "bearer-secret" not in message
    assert "operator@example.test" not in message
    assert "https://***@example.test/model" in message
    assert "api_key=***" in message
    assert "access_token=***" in message
    assert "authorization=Bearer ***" in message
    assert "[redacted-email]" in message


def test_run_build_phase_rejects_malformed_identifiers() -> None:
    result = builds.run_build_phase.run(
        {"id": "project-1"},  # type: ignore[arg-type]
        1,
        "user-1",
    )

    assert result == {"status": "error", "reason": "malformed_identifiers"}


def test_run_build_phase_rejects_whitespace_identifiers() -> None:
    result = builds.run_build_phase.run(
        "project-1 bad",
        1,
        "user-1",
    )

    assert result == {"status": "error", "reason": "malformed_identifiers"}


def test_run_build_phase_marks_project_failed_when_next_phase_enqueue_fails(
    monkeypatch,
    caplog,
) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": 2,
            "project_plan": {},
            "stack": {},
        }
    )
    enqueue_calls: list[tuple[list[str], int, str]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )

    def fake_apply_async(*, args, countdown, queue):
        enqueue_calls.append((args, countdown, queue))
        raise RuntimeError(
            "queue unavailable redis://user:secret@redis.example/0"
        )

    monkeypatch.setattr(builds.run_build_phase, "apply_async", fake_apply_async)
    caplog.set_level(logging.WARNING, logger="codey.saas.tasks.builds")

    result = builds.run_build_phase.run("project-1", 1, "user-1")

    assert enqueue_calls == [(["project-1", 2, "user-1"], 5, "builds")]
    assert result == {
        "status": "error",
        "reason": "next_phase_enqueue_failed",
        "phase": 1,
    }
    assert any(
        "UPDATE build_projects SET status = 'failed' WHERE id = :pid" in sql
        and params == {"pid": "project-1"}
        for sql, params in session.executed
    )
    assert session.commits == 3
    assert "secret" not in caplog.text
    assert "redis://***@redis.example/0" in caplog.text
    assert "Traceback" not in caplog.text


def test_run_build_phase_redacts_missing_project_identifier(monkeypatch, caplog) -> None:
    project_id = "https://user:project-secret@example.test/build?token=project-token"
    session = _FakeSession(None)

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    caplog.set_level(logging.WARNING, logger="codey.saas.tasks.builds")

    result = builds.run_build_phase.run(project_id, 1, "user-1")

    assert result == {"status": "error", "reason": "project_not_found"}
    assert "project-secret" not in caplog.text
    assert "project-token" not in caplog.text
    assert "https://***@example.test/build?token=***" in caplog.text
    assert "Traceback" not in caplog.text


def test_run_build_phase_redacts_malformed_build_file_rows(
    monkeypatch,
    caplog,
) -> None:
    project_id = "https://user:project-secret@example.test/build?token=project-token"
    session = _FakeSession(
        {
            "id": project_id,
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": 1,
            "project_plan": {},
            "stack": {},
        },
        file_rows=[
            {
                "id": {
                    "value": (
                        "https://user:file-secret@example.test/file"
                        "?access_token=file-token"
                    ),
                },
                "file_path": "app/main.py",
            },
        ],
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("generation should not run for malformed file rows")
        ),
    )
    caplog.set_level(logging.WARNING, logger="codey.saas.tasks.builds")

    result = builds.run_build_phase.run(project_id, 1, "user-1")

    assert result == {
        "status": "error",
        "reason": "file_generation_failed",
        "phase": 1,
    }
    assert "project-secret" not in caplog.text
    assert "project-token" not in caplog.text
    assert "file-secret" not in caplog.text
    assert "file-token" not in caplog.text
    assert "https://***@example.test/build?token=***" in caplog.text
    assert "https://***@example.test/file?access_token=***" in caplog.text
    assert "Traceback" not in caplog.text


def test_run_build_phase_tolerates_string_total_phases(monkeypatch) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": "2",
            "project_plan": {},
            "stack": {},
        }
    )
    enqueue_calls: list[tuple[list[str], int, str]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )

    def fake_apply_async(*, args, countdown, queue):
        enqueue_calls.append((args, countdown, queue))

    monkeypatch.setattr(builds.run_build_phase, "apply_async", fake_apply_async)

    result = builds.run_build_phase.run("project-1", 1, "user-1")

    assert result == {
        "status": "phase_completed",
        "phase": 1,
        "next_phase": 2,
    }
    assert enqueue_calls == [(["project-1", 2, "user-1"], 5, "builds")]
    assert session.commits == 2


def test_run_build_phase_tolerates_string_phase_number(monkeypatch) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": 2,
            "project_plan": {},
            "stack": {},
        }
    )
    enqueue_calls: list[tuple[list[str], int, str]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )

    def fake_apply_async(*, args, countdown, queue):
        enqueue_calls.append((args, countdown, queue))

    monkeypatch.setattr(builds.run_build_phase, "apply_async", fake_apply_async)

    result = builds.run_build_phase.run("project-1", "1", "user-1")

    assert result == {
        "status": "phase_completed",
        "phase": 1,
        "next_phase": 2,
    }
    assert enqueue_calls == [(["project-1", 2, "user-1"], 5, "builds")]
    assert any(
        "SET current_phase = :phase" in sql
        and params == {"phase": 1, "pid": "project-1"}
        for sql, params in session.executed
    )
    assert any(
        "FROM build_files" in sql
        and params == {"pid": "project-1", "phase": 1}
        for sql, params in session.executed
    )


def test_run_build_phase_skips_failed_projects(monkeypatch) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "failed",
            "current_phase": 1,
            "total_phases": 2,
            "project_plan": {},
            "stack": {},
        }
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )

    result = builds.run_build_phase.run("project-1", 2, "user-1")

    assert result == {"status": "skipped", "reason": "project is failed"}
    assert not any("SET current_phase = :phase" in sql for sql, _params in session.executed)
    assert not any("FROM build_files" in sql for sql, _params in session.executed)
    assert session.commits == 0


def test_run_build_phase_skips_phase_numbers_beyond_total(monkeypatch) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": 2,
            "project_plan": {},
            "stack": {},
        }
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )

    result = builds.run_build_phase.run("project-1", 3, "user-1")

    assert result == {
        "status": "skipped",
        "reason": "phase_out_of_range",
        "phase": 3,
        "total_phases": 2,
    }
    assert not any("SET current_phase = :phase" in sql for sql, _params in session.executed)
    assert not any("FROM build_files" in sql for sql, _params in session.executed)
    assert session.commits == 0


def test_run_build_phase_skips_stale_phase_numbers(monkeypatch) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "building",
            "current_phase": 2,
            "total_phases": 3,
            "project_plan": {},
            "stack": {},
        }
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )

    result = builds.run_build_phase.run("project-1", 1, "user-1")

    assert result == {
        "status": "skipped",
        "reason": "stale_phase",
        "phase": 1,
        "current_phase": 2,
    }
    assert not any("SET current_phase = :phase" in sql for sql, _params in session.executed)
    assert not any("FROM build_files" in sql for sql, _params in session.executed)
    assert session.commits == 0


def test_run_build_phase_skips_duplicate_phase_with_no_pending_files(
    monkeypatch,
    caplog,
) -> None:
    project_id = "https://user:project-secret@example.test/build?token=project-token"
    session = _FakeSession(
        {
            "id": project_id,
            "name": "Demo build",
            "status": "building",
            "current_phase": 1,
            "total_phases": 2,
            "project_plan": {},
            "stack": {},
        }
    )
    enqueue_calls: list[tuple[list[str], int, str]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )

    def fake_apply_async(*, args, countdown, queue):
        enqueue_calls.append((args, countdown, queue))

    monkeypatch.setattr(builds.run_build_phase, "apply_async", fake_apply_async)
    caplog.set_level(logging.INFO, logger="codey.saas.tasks.builds")

    result = builds.run_build_phase.run(project_id, 1, "user-1")

    assert result == {
        "status": "skipped",
        "reason": "no_pending_files",
        "phase": 1,
    }
    assert enqueue_calls == []
    assert not any("INSERT INTO build_checkpoints" in sql for sql, _params in session.executed)
    assert session.commits == 1
    assert "project-secret" not in caplog.text
    assert "project-token" not in caplog.text
    assert "https://***@example.test/build?token=***" in caplog.text


def test_run_build_phase_tolerates_missing_status_and_name(monkeypatch) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "current_phase": 0,
            "total_phases": 1,
            "project_plan": {},
            "stack": {},
        }
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )

    result = builds.run_build_phase.run("project-1", 1, "user-1")

    assert result == {
        "status": "error",
        "reason": "file_generation_failed",
        "phase": 1,
    }
    assert session.commits == 2


def test_run_build_phase_marks_project_failed_when_model_resolution_fails(
    monkeypatch,
    caplog,
) -> None:
    project_id = "https://user:project-secret@example.test/build?token=project-token"
    session = _FakeSession(
        {
            "id": project_id,
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": 1,
            "project_plan": {},
            "stack": {},
        }
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: (_ for _ in ()).throw(
            RuntimeError(
                "no model configured https://user:model-secret@example.test/model"
                "?api_key=model-token"
            )
        ),
    )
    caplog.set_level(logging.WARNING, logger="codey.saas.tasks.builds")

    result = builds.run_build_phase.run(project_id, 1, "user-1")

    assert result == {
        "status": "error",
        "reason": "model_resolution_failed",
        "phase": 1,
    }
    assert any(
        "UPDATE build_projects SET status = 'failed' WHERE id = :pid" in sql
        and params == {"pid": project_id}
        for sql, params in session.executed
    )
    assert not any("FROM build_files" in sql for sql, _params in session.executed)
    assert session.commits == 2
    assert "project-secret" not in caplog.text
    assert "project-token" not in caplog.text
    assert "model-secret" not in caplog.text
    assert "model-token" not in caplog.text
    assert "https://***@example.test/build?token=***" in caplog.text
    assert "https://***@example.test/model?api_key=***" in caplog.text


def test_run_build_phase_treats_malformed_project_row_as_not_found(monkeypatch) -> None:
    session = _FakeSession("bad-row")

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )

    result = builds.run_build_phase.run("project-1", 1, "user-1")

    assert result == {"status": "error", "reason": "project_not_found"}
    assert not any("FROM build_files" in sql for sql, _params in session.executed)
    assert session.commits == 0


def test_run_build_phase_marks_malformed_file_paths_failed_without_generation(monkeypatch) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": 1,
            "project_plan": {},
            "stack": {},
        },
        file_rows=[
            {"id": "file-1", "file_path": {"path": "app/main.py"}},
            {"id": "file-2", "file_path": "../app/secret.py"},
        ],
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("generation should not run for malformed file rows")
        ),
    )

    result = builds.run_build_phase.run("project-1", 1, "user-1")

    assert result == {
        "status": "error",
        "reason": "file_generation_failed",
        "phase": 1,
    }
    assert any(
        "UPDATE build_files SET status = 'failed' WHERE id = :fid" in sql
        and params == {"fid": "file-1"}
        for sql, params in session.executed
    )
    assert any(
        "UPDATE build_files SET status = 'failed' WHERE id = :fid" in sql
        and params == {"fid": "file-2"}
        for sql, params in session.executed
    )
    assert not any(
        "UPDATE build_files SET content = :content" in sql
        for sql, _params in session.executed
    )
    assert session.commits == 2


def test_run_build_phase_fails_all_malformed_file_ids_without_generation(monkeypatch) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": 1,
            "project_plan": {},
            "stack": {},
        },
        file_rows=[
            {"id": {"bad": "id"}, "file_path": "app/main.py"},
            {"id": "file-1\nbad", "file_path": "app/other.py"},
        ],
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("generation should not run for malformed file ids")
        ),
    )

    result = builds.run_build_phase.run("project-1", 1, "user-1")

    assert result == {
        "status": "error",
        "reason": "file_generation_failed",
        "phase": 1,
    }
    assert not any(
        "UPDATE build_files SET status = 'failed' WHERE id = :fid" in sql
        for sql, _params in session.executed
    )
    assert not any(
        "UPDATE build_files SET content = :content" in sql
        for sql, _params in session.executed
    )
    assert session.commits == 2


def test_run_build_phase_marks_project_failed_when_all_generation_fails(
    monkeypatch,
) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": 1,
            "project_plan": {},
            "stack": {},
        },
        file_rows=[
            {"id": "file-1", "file_path": "app/main.py"},
        ],
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )

    async def fail_call_model(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fail_call_model,
    )

    result = builds.run_build_phase.run("project-1", 1, "user-1")

    assert result == {
        "status": "error",
        "reason": "file_generation_failed",
        "phase": 1,
    }
    assert any(
        "UPDATE build_files SET status = 'failed' WHERE id = :fid" in sql
        and params == {"fid": "file-1"}
        for sql, params in session.executed
    )
    assert any(
        "UPDATE build_projects SET status = 'failed' WHERE id = :pid" in sql
        and params == {"pid": "project-1"}
        for sql, params in session.executed
    )
    assert not any("INSERT INTO build_checkpoints" in sql for sql, _params in session.executed)
    assert not any("SET status = 'completed'" in sql for sql, _params in session.executed)
    assert session.commits == 2


def test_run_build_phase_marks_project_failed_when_any_generation_fails(
    monkeypatch,
) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": 1,
            "project_plan": {},
            "stack": {},
        },
        file_rows=[
            {"id": "file-ok", "file_path": "app/main.py"},
            {"id": "file-fail", "file_path": "app/broken.py"},
        ],
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )

    async def partially_fail_call_model(*args, **kwargs):
        messages = args[2]
        if "app/broken.py" in messages[1]["content"]:
            raise RuntimeError("model unavailable")
        return "print('ok')"

    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        partially_fail_call_model,
    )

    result = builds.run_build_phase.run("project-1", 1, "user-1")

    assert result == {
        "status": "error",
        "reason": "file_generation_failed",
        "phase": 1,
    }
    assert any(
        "UPDATE build_files SET content = :content, line_count = :lines" in sql
        and params == {"content": "print('ok')", "lines": 1, "fid": "file-ok"}
        for sql, params in session.executed
    )
    assert any(
        "UPDATE build_files SET status = 'failed' WHERE id = :fid" in sql
        and params == {"fid": "file-fail"}
        for sql, params in session.executed
    )
    assert any(
        "UPDATE build_projects SET status = 'failed' WHERE id = :pid" in sql
        and params == {"pid": "project-1"}
        for sql, params in session.executed
    )
    assert not any("INSERT INTO build_checkpoints" in sql for sql, _params in session.executed)
    assert not any("SET status = 'completed'" in sql for sql, _params in session.executed)
    assert session.commits == 2


def test_run_build_phase_redacts_file_path_on_generation_failure(
    monkeypatch,
    caplog,
) -> None:
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": 1,
            "project_plan": {},
            "stack": {},
        },
        file_rows=[
            {"id": "file-1", "file_path": "app/main.py?access_token=file-secret"},
        ],
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )

    async def fail_call_model(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fail_call_model,
    )
    caplog.set_level(logging.WARNING, logger="codey.saas.tasks.builds")

    result = builds.run_build_phase.run("project-1", 1, "user-1")

    assert result == {
        "status": "error",
        "reason": "file_generation_failed",
        "phase": 1,
    }
    assert "file-secret" not in caplog.text
    assert "app/main.py?access_token=***" in caplog.text


def test_run_build_phase_accepts_mapping_model_output(monkeypatch) -> None:
    file_id = uuid.uuid4()
    session = _FakeSession(
        {
            "id": "project-1",
            "name": "Demo build",
            "status": "planning",
            "current_phase": 0,
            "total_phases": 1,
            "project_plan": {},
            "stack": {},
        },
        file_rows=[
            "bad-row",
            {"id": file_id, "file_path": "app/main.py"},
        ],
    )

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.intelligence.providers.resolve_model",
        lambda _kind: ("provider", "model"),
    )

    async def fake_call_model(*args, **kwargs):
        return {"content": "print('ok')"}

    monkeypatch.setattr(
        "codey.saas.intelligence.providers.call_model",
        fake_call_model,
    )

    result = builds.run_build_phase.run("project-1", 1, "user-1")

    assert result == {
        "status": "completed",
        "phase": 1,
    }
    assert any(
        "UPDATE build_files SET content = :content, line_count = :lines" in sql
        and params == {"content": "print('ok')", "lines": 1, "fid": str(file_id)}
        for sql, params in session.executed
    )
    assert not any(
        "UPDATE build_files SET status = 'failed' WHERE id = :fid" in sql
        and params == {"fid": str(file_id)}
        for sql, params in session.executed
    )
    assert session.commits == 3
