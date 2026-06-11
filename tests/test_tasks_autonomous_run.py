from __future__ import annotations

import logging

import codey.saas.tasks.autonomous as autonomous


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self) -> _FakeMappingsResult:
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, repo_row):
        self._repo_row = repo_row
        self._calls = 0
        self.update_params = None
        self.committed = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, _statement, params=None):
        self._calls += 1
        if self._calls == 1:
            return _FakeMappingsResult([self._repo_row])
        self.update_params = params
        return _FakeMappingsResult([])

    async def commit(self) -> None:
        self.committed = True


class _FailingUpdateSession(_FakeSession):
    async def execute(self, _statement, params=None):
        self._calls += 1
        if self._calls == 1:
            return _FakeMappingsResult([self._repo_row])
        raise RuntimeError("database write failed token=secret")


class _FakePhase:
    value = "observe"


class _FakeSweepResult:
    es_score = 0.42
    phase = _FakePhase()


class _FakeSweep:
    def run(self, graph):
        return _FakeSweepResult()


class _FakeComponent:
    def __init__(self, stress: float) -> None:
        self.node_id = "node-1"
        self.file_path = "app.py"
        self.stress = stress
        self.risk_level = "high"


class _FakeRepoState:
    def __init__(self, stress: float) -> None:
        component = _FakeComponent(stress)
        self.components = [component]
        self.hotspots = [component]


class _FakeCandidate:
    target_node_id = "node-1"
    kind = "extract_method"
    predicted_repo_es_delta = 0.2
    description = "Extract complex logic"


class _FakeController:
    def __init__(self, *_args, **_kwargs) -> None:
        self.repo_state = _FakeRepoState(0.8)

    def analyze(self, graph, goal=None):
        return self.repo_state

    def rank_interventions(self, graph, goal=None, repo_state=None, limit=5):
        return [_FakeCandidate()]


def test_run_autonomous_repo_skips_malformed_identifiers() -> None:
    result = autonomous.run_autonomous_repo.run(
        {"id": "repo-1"},  # type: ignore[arg-type]
        "user-1",
    )

    assert result == {"status": "skipped", "repo_id": "unknown"}


def test_coerce_autonomous_identifier_rejects_malformed_text() -> None:
    assert autonomous._coerce_autonomous_identifier(" repo-1 ") == "repo-1"
    assert autonomous._coerce_autonomous_identifier(123) == "123"
    assert autonomous._coerce_autonomous_identifier(True) is None
    assert autonomous._coerce_autonomous_identifier("repo 1") is None
    assert autonomous._coerce_autonomous_identifier("repo\t1") is None
    assert autonomous._coerce_autonomous_identifier("repo\n1") is None


def test_run_autonomous_repo_redacts_missing_repo_identifier(
    monkeypatch,
    caplog,
) -> None:
    repo_id = "https://user:repo-secret@example.test/repo.git?token=repo-token"
    session = _FakeSession(None)

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    caplog.set_level(logging.WARNING, logger="codey.saas.tasks.autonomous")

    result = autonomous.run_autonomous_repo.run(repo_id, "user-1")

    assert result == {"status": "skipped", "repo_id": repo_id}
    assert "repo-secret" not in caplog.text
    assert "repo-token" not in caplog.text
    assert "https://***@example.test/repo.git?token=***" in caplog.text
    assert "Traceback" not in caplog.text


def test_run_autonomous_repo_tolerates_non_dict_config(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": "corrupt-config",
        "github_token": None,
    }
    session = _FakeSession(repo_row)

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: {"clone_url": clone_url, "token": token},
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _FakeSweep)
    monkeypatch.setattr("codey.nfet.controller.NFETController", _FakeController)

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result["status"] == "completed"
    assert result["repo_id"] == "repo-1"
    assert result["phase"] == "observe"
    assert result["high_stress_count"] == 1
    assert result["improvements"] == [
        {
            "component": "app.py",
            "stress": 0.8,
            "risk": "high",
            "recommended_action": "extract_method",
            "delta_es": 0.2,
            "summary": "Extract complex logic",
        }
    ]
    assert session.update_params == {"phase": "observe", "es": 0.42, "rid": "repo-1"}
    assert session.committed is True


def test_run_autonomous_repo_tolerates_invalid_threshold(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {"stress_trigger": "not-a-number"},
        "github_token": None,
    }
    session = _FakeSession(repo_row)

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: {"clone_url": clone_url, "token": token},
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _FakeSweep)
    monkeypatch.setattr("codey.nfet.controller.NFETController", _FakeController)

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result["status"] == "completed"
    assert len(result["improvements"]) == 1


def test_run_autonomous_repo_drops_malformed_decrypted_tokens(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": "encrypted-token",
    }
    session = _FakeSession(repo_row)
    loader_calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.security.encryption.decrypt_token",
        lambda _token: {"token": "not-a-string"},
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: loader_calls.append((clone_url, token)) or {},
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _FakeSweep)
    monkeypatch.setattr("codey.nfet.controller.NFETController", _FakeController)

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result["status"] == "completed"
    assert loader_calls == [("https://github.com/owner/repo.git", None)]


def test_coerce_autonomous_github_token_rejects_malformed_text() -> None:
    assert autonomous._coerce_autonomous_github_token(" ghp_valid ") == "ghp_valid"
    assert autonomous._coerce_autonomous_github_token("ghp_valid bad") is None
    assert autonomous._coerce_autonomous_github_token("ghp_valid\tbad") is None
    assert autonomous._coerce_autonomous_github_token("ghp_valid\x00bad") is None
    assert autonomous._coerce_autonomous_github_token("ghp_valid\x7fbad") is None


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://github.com/owner/repo.git?access_token=secret",
        "https://github.com/owner/repo.git#readme",
        "https://user:secret@github.com/owner/repo.git",
        "ssh://git:secret@github.com/owner/repo.git",
        "ssh://root@github.com/owner/repo.git",
        "git://root@github.com/owner/repo.git",
        "git+ssh://root@github.com/owner/repo.git",
        "file:///tmp/repo.git",
        "ftp://github.com/owner/repo.git",
        "javascript://github.com/owner/repo.git",
        "https://github.com:not-a-port/owner/repo.git",
        "https:///owner/repo.git",
        "owner/repo",
        "/tmp/repo.git",
        "github.com:owner/repo.git",
        "git@gitlab.com:owner/repo.git",
        "https://github.com/owner/repo .git",
        "git@github.com:owner/repo .git",
    ],
)
def test_coerce_autonomous_clone_url_rejects_malformed_url_shapes(
    clone_url: str,
) -> None:
    assert autonomous._coerce_autonomous_clone_url(clone_url) is None


@pytest.mark.parametrize(
    "clone_url",
    [
        "git@github.com:owner/repo.git",
        "ssh://git@github.com/owner/repo.git",
        "git+ssh://git@github.com/owner/repo.git",
    ],
)
def test_coerce_autonomous_clone_url_accepts_safe_ssh_git_urls(
    clone_url: str,
) -> None:
    assert (
        autonomous._coerce_autonomous_clone_url(clone_url)
        == clone_url
    )


def test_redact_autonomous_error_hides_common_secret_shapes() -> None:
    message = autonomous._redact_autonomous_error(
        "clone failed https://user:url-secret@example.test/repo.git"
        "?access_token=query-secret authorization=Bearer bearer-secret "
        "mirror=https://example.test/repo.git#client_secret=fragment-secret "
        "for operator@example.test",
    )

    assert "url-secret" not in message
    assert "query-secret" not in message
    assert "fragment-secret" not in message
    assert "bearer-secret" not in message
    assert "operator@example.test" not in message
    assert "https://***@example.test/repo.git" in message
    assert "access_token=***" in message
    assert "client_secret=***" in message
    assert "authorization=Bearer ***" in message
    assert "[redacted-email]" in message


def test_run_autonomous_repo_redacts_unexpected_load_error_logs(
    monkeypatch,
    caplog,
) -> None:
    repo_id = "https://user:repo-secret@example.test/repo.git?token=repo-token"
    repo_row = {
        "id": repo_id,
        "full_name": (
            "https://user:name-secret@example.test/repo.git?token=name-token"
        ),
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": None,
    }
    session = _FakeSession(repo_row)

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )

    def fail_load_repo(_clone_url, token=None):
        raise TypeError(
            "clone exploded https://user:secret@example.test/repo.git"
        )

    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        fail_load_repo,
    )
    caplog.set_level(logging.INFO, logger="codey.saas.tasks.autonomous")

    result = autonomous.run_autonomous_repo.run(repo_id, "user-1")

    assert result == {
        "status": "failed",
        "repo_id": repo_id,
        "reason": "repo_load_failed",
    }
    assert "repo-secret" not in caplog.text
    assert "repo-token" not in caplog.text
    assert "name-secret" not in caplog.text
    assert "name-token" not in caplog.text
    assert "secret" not in caplog.text
    assert "https://***@example.test/repo.git?token=***" in caplog.text
    assert "https://***@example.test/repo.git" in caplog.text
    assert "Traceback" not in caplog.text


def test_run_autonomous_repo_drops_line_break_github_tokens(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": "ghp_valid\r\nX-Injected: value",
    }
    session = _FakeSession(repo_row)
    loader_calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: loader_calls.append((clone_url, token)) or {},
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _FakeSweep)
    monkeypatch.setattr("codey.nfet.controller.NFETController", _FakeController)

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result["status"] == "completed"
    assert loader_calls == [("https://github.com/owner/repo.git", None)]


def test_run_autonomous_repo_drops_line_break_decrypted_tokens(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": "encrypted-token",
    }
    session = _FakeSession(repo_row)
    loader_calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.saas.security.encryption.decrypt_token",
        lambda _token: "ghp_valid\r\nX-Injected: value",
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: loader_calls.append((clone_url, token)) or {},
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _FakeSweep)
    monkeypatch.setattr("codey.nfet.controller.NFETController", _FakeController)

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result["status"] == "completed"
    assert loader_calls == [("https://github.com/owner/repo.git", None)]


def test_coerce_autonomous_float_rejects_overflowing_values() -> None:
    assert autonomous._coerce_autonomous_float(True, default=0.7) == 0.7
    assert autonomous._coerce_autonomous_float(10**10000, default=0.7) == 0.7


def test_autonomous_result_numeric_helpers_bound_extreme_values() -> None:
    assert autonomous._coerce_autonomous_unit_float(True, default=0.7) == 0.7
    assert autonomous._coerce_autonomous_unit_float("1e300", default=0.7) == 1.0
    assert autonomous._coerce_autonomous_unit_float("-0.1", default=0.7) == 0.7
    assert autonomous._coerce_autonomous_delta(True, default=0.2) == 0.2
    assert autonomous._coerce_autonomous_delta("1e300") == 1.0
    assert autonomous._coerce_autonomous_delta("-1e300") == -1.0
    assert autonomous._coerce_stress_threshold(True) == 0.7


def test_run_autonomous_repo_matches_candidate_targets_from_hotspots(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {"stress_trigger": 0.7},
        "github_token": None,
    }
    session = _FakeSession(repo_row)

    class _HotspotOnlyRepoState:
        def __init__(self) -> None:
            self.components = []
            self.hotspots = [_FakeComponent(0.85)]

    class _HotspotOnlyController:
        def __init__(self, *_args, **_kwargs) -> None:
            self.repo_state = _HotspotOnlyRepoState()

        def analyze(self, graph, goal=None):
            return self.repo_state

        def rank_interventions(self, graph, goal=None, repo_state=None, limit=5):
            return [_FakeCandidate()]

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: {"clone_url": clone_url, "token": token},
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _FakeSweep)
    monkeypatch.setattr(
        "codey.nfet.controller.NFETController",
        _HotspotOnlyController,
    )

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result["high_stress_count"] == 1
    assert result["improvements"] == [
        {
            "component": "app.py",
            "stress": 0.85,
            "risk": "high",
            "recommended_action": "extract_method",
            "delta_es": 0.2,
            "summary": "Extract complex logic",
        }
    ]


def test_run_autonomous_repo_bounds_ignored_candidate_limit(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {"stress_trigger": 0.7},
        "github_token": None,
    }
    session = _FakeSession(repo_row)

    class _ManyCandidateController(_FakeController):
        def rank_interventions(self, graph, goal=None, repo_state=None, limit=5):
            return [_FakeCandidate() for _ in range(limit * 4)]

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: {"clone_url": clone_url, "token": token},
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _FakeSweep)
    monkeypatch.setattr(
        "codey.nfet.controller.NFETController",
        _ManyCandidateController,
    )

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result["status"] == "completed"
    assert len(result["improvements"]) == autonomous._AUTONOMOUS_IMPROVEMENT_LIMIT


def test_run_autonomous_repo_skips_invalid_clone_url(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": {"url": "https://github.com/owner/repo.git"},
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": None,
    }
    session = _FakeSession(repo_row)
    loader_calls: list[tuple[object, object]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: loader_calls.append((clone_url, token)),
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _FakeSweep)
    monkeypatch.setattr("codey.nfet.controller.NFETController", _FakeController)

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result == {"status": "skipped", "repo_id": "repo-1"}
    assert loader_calls == []
    assert session.update_params is None
    assert session.committed is False


def test_run_autonomous_repo_skips_control_character_clone_url(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git\r\nbad",
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": None,
    }
    session = _FakeSession(repo_row)
    loader_calls: list[tuple[object, object]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: loader_calls.append((clone_url, token)),
    )

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result == {"status": "skipped", "repo_id": "repo-1"}
    assert loader_calls == []
    assert session.update_params is None
    assert session.committed is False


def test_run_autonomous_repo_skips_malformed_clone_url_before_loader(
    monkeypatch,
) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git?access_token=secret",
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": None,
    }
    session = _FakeSession(repo_row)
    loader_calls: list[tuple[object, object]] = []

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: loader_calls.append((clone_url, token)),
    )

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result == {"status": "skipped", "repo_id": "repo-1"}
    assert loader_calls == []
    assert session.update_params is None
    assert session.committed is False


def test_run_autonomous_repo_skips_loader_clone_url_value_errors(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com:not-a-port/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": None,
    }
    session = _FakeSession(repo_row)

    def reject_clone_url(*_args, **_kwargs):
        raise ValueError("Port could not be cast to integer value")

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        reject_clone_url,
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _FakeSweep)
    monkeypatch.setattr("codey.nfet.controller.NFETController", _FakeController)

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result == {"status": "skipped", "repo_id": "repo-1"}
    assert session.update_params is None
    assert session.committed is False


def test_run_autonomous_repo_returns_failed_on_clone_runtime_error(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": None,
    }
    session = _FakeSession(repo_row)

    def fail_clone(*_args, **_kwargs):
        raise RuntimeError("git clone timed out after 180s")

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        fail_clone,
    )

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result == {
        "status": "failed",
        "repo_id": "repo-1",
        "reason": "clone_failed",
    }
    assert session.update_params is None
    assert session.committed is False


def test_run_autonomous_repo_returns_failed_on_unexpected_loader_error(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": None,
    }
    session = _FakeSession(repo_row)

    def fail_load(*_args, **_kwargs):
        raise OSError("temporary clone directory unavailable")

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        fail_load,
    )

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result == {
        "status": "failed",
        "repo_id": "repo-1",
        "reason": "repo_load_failed",
    }
    assert session.update_params is None
    assert session.committed is False


def test_run_autonomous_repo_returns_failed_on_analysis_error(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": None,
    }
    session = _FakeSession(repo_row)

    class _FailingSweep:
        def run(self, graph):
            raise RuntimeError("invalid graph shape")

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: {"clone_url": clone_url, "token": token},
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _FailingSweep)
    monkeypatch.setattr("codey.nfet.controller.NFETController", _FakeController)

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result == {
        "status": "failed",
        "repo_id": "repo-1",
        "reason": "analysis_failed",
    }
    assert session.update_params is None
    assert session.committed is False


def test_run_autonomous_repo_returns_failed_on_health_update_error(
    monkeypatch,
    caplog,
) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {},
        "github_token": None,
    }
    session = _FailingUpdateSession(repo_row)

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: {"clone_url": clone_url, "token": token},
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _FakeSweep)
    monkeypatch.setattr("codey.nfet.controller.NFETController", _FakeController)
    caplog.set_level(logging.WARNING, logger="codey.saas.tasks.autonomous")

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result == {
        "status": "failed",
        "repo_id": "repo-1",
        "reason": "health_update_failed",
    }
    assert "token=secret" not in caplog.text
    assert "token=***" in caplog.text
    assert session.update_params is None
    assert session.committed is False


def test_run_autonomous_repo_skips_malformed_repo_row(monkeypatch) -> None:
    session = _FakeSession("bad-row")

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result == {"status": "skipped", "repo_id": "repo-1"}
    assert session.update_params is None
    assert session.committed is False


def test_run_autonomous_repo_normalizes_malformed_analysis_fields(monkeypatch) -> None:
    repo_row = {
        "id": "repo-1",
        "full_name": "owner/repo",
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "autonomous_config": {"stress_trigger": "0.7"},
        "github_token": None,
    }
    session = _FakeSession(repo_row)

    class _MalformedPhase:
        value = {"phase": "observe"}

    class _MalformedSweepResult:
        es_score = "0.42"
        phase = _MalformedPhase()

    class _MalformedSweep:
        def run(self, graph):
            return _MalformedSweepResult()

    class _MalformedComponent:
        node_id = "node-1"
        file_path = {"path": "app.py"}
        stress = "0.8"
        risk_level = {"risk": "high"}

    class _MalformedRepoState:
        components = [_MalformedComponent()]
        hotspots = {"bad": "shape"}

    class _MalformedCandidate:
        target_node_id = "node-1"
        kind = {"kind": "extract_method"}
        predicted_repo_es_delta = "0.2"
        description = ["Extract complex logic"]

    class _MalformedController:
        def __init__(self, *_args, **_kwargs) -> None:
            self.repo_state = _MalformedRepoState()

        def analyze(self, graph, goal=None):
            return self.repo_state

        def rank_interventions(self, graph, goal=None, repo_state=None, limit=5):
            return [_MalformedCandidate()]

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )
    monkeypatch.setattr(
        "codey.nfet.repository_loader.build_graph_from_clone_url_sync",
        lambda clone_url, token=None: {"clone_url": clone_url, "token": token},
    )
    monkeypatch.setattr("codey.nfet.sweep.NFETSweep", _MalformedSweep)
    monkeypatch.setattr("codey.nfet.controller.NFETController", _MalformedController)

    result = autonomous.run_autonomous_repo.run("repo-1", "user-1")

    assert result == {
        "status": "completed",
        "repo_id": "repo-1",
        "full_name": "owner/repo",
        "es_score": 0.42,
        "phase": "unknown",
        "high_stress_count": 0,
        "improvements": [
            {
                "component": "unknown",
                "stress": 0.8,
                "risk": "unknown",
                "recommended_action": "unknown",
                "delta_es": 0.2,
                "summary": "",
            }
        ],
    }
    assert session.update_params == {"phase": "unknown", "es": 0.42, "rid": "repo-1"}
    assert session.committed is True
