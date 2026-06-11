from __future__ import annotations

import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import codey.autonomous.monitor as monitor_module
from codey.autonomous.monitor import (
    _FileChangeHandler,
    _coerce_monitor_stress,
    AutonomousConfig,
    AutonomousMonitor,
    Phase,
    TriggerCondition,
)


class _FakeGraph:
    def __init__(self) -> None:
        self.updated: tuple[str, list[object], list[object]] | None = None
        self.removed: str | None = None

    def update_file(self, file_path: str, new_nodes: list[object], new_edges: list[object]) -> None:
        self.updated = (file_path, new_nodes, new_edges)

    def remove_file(self, file_path: str) -> None:
        self.removed = file_path


class _FailingObserver:
    instances: list["_FailingObserver"] = []

    def __init__(self) -> None:
        self.scheduled = False
        self.stopped = False
        self.joined = False
        self.join_timeout: float | None = None
        self.daemon = False
        self.instances.append(self)

    def schedule(self, *args, **kwargs) -> None:
        self.scheduled = True

    def start(self) -> None:
        raise RuntimeError("observer boom")

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        self.joined = True
        self.join_timeout = timeout


class _StartedObserver:
    instances: list["_StartedObserver"] = []

    def __init__(self) -> None:
        self.scheduled = False
        self.started = False
        self.stopped = False
        self.joined = False
        self.join_timeout: float | None = None
        self.daemon = False
        self.instances.append(self)

    def schedule(self, *args, **kwargs) -> None:
        self.scheduled = True

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        self.joined = True
        self.join_timeout = timeout


class _CleanupFailingObserver(_StartedObserver):
    instances: list["_CleanupFailingObserver"] = []

    def stop(self) -> None:
        self.stopped = True
        raise RuntimeError(
            "cleanup failed for https://user:secret@example.test/repo.git"
        )


class _FailingThread:
    instances: list["_FailingThread"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.started = False
        self.instances.append(self)

    def start(self) -> None:
        self.started = True
        raise RuntimeError("thread boom")


class _JoinFailingThread:
    def __init__(self) -> None:
        self.joined = False
        self.join_timeout: float | None = None

    def join(self, timeout: float | None = None) -> None:
        self.joined = True
        self.join_timeout = timeout
        raise RuntimeError("thread join failed with token=abc123")


def _make_startable_monitor() -> AutonomousMonitor:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor.config = AutonomousConfig(sweep_interval=1)
    monitor._running = False
    monitor._watchers = []
    monitor._sweep_thread = None
    monitor._watch_path = None
    monitor._on_file_change = lambda _path: None
    monitor._sweep_loop = lambda: None
    return monitor


def test_file_change_handler_dispatches_file_moves() -> None:
    paths: list[str] = []
    handler = _FileChangeHandler(paths.append)

    handler.on_moved(
        SimpleNamespace(
            is_directory=False,
            src_path="/repo/src/old.py",
            dest_path="/repo/src/new.py",
        )
    )

    assert paths == ["/repo/src/old.py", "/repo/src/new.py"]


def test_file_change_handler_ignores_directory_moves() -> None:
    paths: list[str] = []
    handler = _FileChangeHandler(paths.append)

    handler.on_moved(
        SimpleNamespace(
            is_directory=True,
            src_path="/repo/src",
            dest_path="/repo/lib",
        )
    )

    assert paths == []


def test_start_resets_state_when_observer_start_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _FailingObserver.instances = []
    monkeypatch.setattr(monitor_module, "_WATCHDOG_IMPORT_ERROR", None)
    monkeypatch.setattr(monitor_module, "Observer", _FailingObserver)

    monitor = _make_startable_monitor()

    with pytest.raises(RuntimeError, match="observer boom"):
        monitor.start(tmp_path)

    observer = _FailingObserver.instances[0]
    assert observer.scheduled is True
    assert observer.stopped is True
    assert observer.joined is True
    assert observer.join_timeout == 5.0
    assert monitor._running is False
    assert monitor._watchers == []
    assert monitor._sweep_thread is None
    assert monitor._watch_path is None


def test_start_resets_state_when_sweep_thread_start_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _StartedObserver.instances = []
    _FailingThread.instances = []
    monkeypatch.setattr(monitor_module, "_WATCHDOG_IMPORT_ERROR", None)
    monkeypatch.setattr(monitor_module, "Observer", _StartedObserver)
    monkeypatch.setattr(monitor_module.threading, "Thread", _FailingThread)

    monitor = _make_startable_monitor()

    with pytest.raises(RuntimeError, match="thread boom"):
        monitor.start(tmp_path)

    observer = _StartedObserver.instances[0]
    thread = _FailingThread.instances[0]
    assert observer.scheduled is True
    assert observer.started is True
    assert observer.stopped is True
    assert observer.joined is True
    assert observer.join_timeout == 5.0
    assert thread.started is True
    assert monitor._running is False
    assert monitor._watchers == []
    assert monitor._sweep_thread is None
    assert monitor._watch_path is None


def test_start_preserves_thread_error_when_observer_cleanup_fails(
    monkeypatch,
    tmp_path: Path,
    caplog,
) -> None:
    _CleanupFailingObserver.instances = []
    _FailingThread.instances = []
    monkeypatch.setattr(monitor_module, "_WATCHDOG_IMPORT_ERROR", None)
    monkeypatch.setattr(monitor_module, "Observer", _CleanupFailingObserver)
    monkeypatch.setattr(monitor_module.threading, "Thread", _FailingThread)
    caplog.set_level(logging.WARNING, logger="codey.autonomous.monitor")

    monitor = _make_startable_monitor()

    with pytest.raises(RuntimeError, match="thread boom"):
        monitor.start(tmp_path)

    assert "user:secret" not in caplog.text
    assert "secret@example.test" not in caplog.text
    assert "https://***@example.test/repo.git" in caplog.text
    assert monitor._running is False
    assert monitor._watchers == []
    assert monitor._sweep_thread is None
    assert monitor._watch_path is None


def test_stop_clears_state_when_cleanup_fails(caplog) -> None:
    observer = _CleanupFailingObserver()
    thread = _JoinFailingThread()
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor.config = AutonomousConfig(sweep_interval=1)
    monitor._running = True
    monitor._watchers = [observer]
    monitor._sweep_thread = thread
    monitor._watch_path = Path("/repo")
    caplog.set_level(logging.WARNING, logger="codey.autonomous.monitor")

    monitor.stop()

    assert observer.stopped is True
    assert observer.joined is True
    assert observer.join_timeout == 5.0
    assert thread.joined is True
    assert thread.join_timeout == 6
    assert monitor._running is False
    assert monitor._watchers == []
    assert monitor._sweep_thread is None
    assert monitor._watch_path is None
    assert "user:secret" not in caplog.text
    assert "abc123" not in caplog.text
    assert "https://***@example.test/repo.git" in caplog.text
    assert "token=***" in caplog.text


def test_on_file_change_uses_repo_relative_paths(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    file_path = repo_root / "src" / "app.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("print('ok')\n", encoding="utf-8")

    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor.graph = _FakeGraph()
    monitor._parser = SimpleNamespace(
        parse_file=lambda path: (
            [
                SimpleNamespace(
                    id="node-1",
                    kind="file",
                    name="app.py",
                    file_path=str(path),
                    line_start=1,
                    line_end=1,
                    complexity=None,
                    cohesion=None,
                    properties={},
                )
            ],
            [],
        )
    )
    monitor._lock = threading.Lock()
    monitor._watch_path = repo_root.resolve()
    monitor._check_triggers = lambda file_path: []
    monitor._handle_trigger = lambda *args, **kwargs: None

    monitor._on_file_change(str(file_path))

    updated = monitor.graph.updated
    assert updated is not None
    assert updated[0] == "src/app.py"
    assert updated[1][0].file_path == "src/app.py"


def test_on_file_change_removes_deleted_repo_relative_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    file_path = repo_root / "src" / "deleted.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("print('old')\n", encoding="utf-8")
    file_path.unlink()

    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor.graph = _FakeGraph()
    monitor._parser = SimpleNamespace(
        parse_file=lambda path: (_ for _ in ()).throw(
            AssertionError("deleted files should not be parsed")
        )
    )
    monitor._lock = threading.Lock()
    monitor._watch_path = repo_root.resolve()
    monitor._check_triggers = lambda file_path: []
    monitor._handle_trigger = lambda *args, **kwargs: None

    monitor._on_file_change(str(file_path))

    assert monitor.graph.removed == "src/deleted.py"
    assert monitor.graph.updated is None


def test_graph_file_path_ignores_unresolvable_paths(tmp_path: Path) -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor._watch_path = tmp_path.resolve()

    assert monitor._graph_file_path(Path("bad\0path.py")) is None


def test_score_candidate_rejects_malformed_numeric_fields() -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)

    assert monitor._score_candidate({"score": "inf"}, object()) == 0.0
    assert monitor._score_candidate({"score": float("nan")}, object()) == 0.0
    assert monitor._score_candidate(
        {
            "estimated_es_delta": "0.2",
            "estimated_coverage_delta": "nan",
            "estimated_complexity_delta": "bad",
        },
        object(),
    ) == 2.0


def test_score_candidate_bounds_extreme_numeric_fields() -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)

    assert monitor._score_candidate({"score": "1e300"}, object()) == 100.0
    assert monitor._score_candidate({"score": "-1e300"}, object()) == -100.0
    assert (
        monitor._score_candidate(
            {
                "estimated_es_delta": "1e300",
                "estimated_coverage_delta": "1e300",
                "estimated_complexity_delta": "-1e300",
            },
            object(),
        )
        == 100.0
    )


def test_coerce_monitor_stress_preserves_finite_trigger_semantics() -> None:
    assert _coerce_monitor_stress(float("inf")) == 1_000_000.0
    assert _coerce_monitor_stress(10**10000) == 1_000_000.0
    assert _coerce_monitor_stress(float("nan")) == 0.0
    assert _coerce_monitor_stress("bad") == 0.0
    assert _coerce_monitor_stress(-1.0) == 0.0
    assert _coerce_monitor_stress(-(10**10000)) == 0.0
    assert _coerce_monitor_stress("0.8") == 0.8


def test_autonomous_config_normalizes_invalid_sweep_intervals() -> None:
    assert AutonomousConfig(sweep_interval=0).sweep_interval == 60
    assert AutonomousConfig(sweep_interval=-1).sweep_interval == 60
    assert AutonomousConfig(sweep_interval=True).sweep_interval == 60
    assert AutonomousConfig(sweep_interval="2").sweep_interval == 2  # type: ignore[arg-type]
    assert AutonomousConfig(sweep_interval=10**10000).sweep_interval == 86_400


def test_autonomous_config_normalizes_impact_radius_guardrail() -> None:
    assert AutonomousConfig(max_impact_radius=0).max_impact_radius == 15
    assert AutonomousConfig(max_impact_radius=-1).max_impact_radius == 15
    assert AutonomousConfig(max_impact_radius=True).max_impact_radius == 15
    assert AutonomousConfig(max_impact_radius="3").max_impact_radius == 3  # type: ignore[arg-type]
    assert AutonomousConfig(max_impact_radius=10**10000).max_impact_radius == 10_000


def test_autonomous_config_normalizes_unit_interval_thresholds() -> None:
    assert AutonomousConfig(stress_threshold=-0.1).stress_threshold == 0.7
    assert AutonomousConfig(stress_threshold=1.1).stress_threshold == 0.7
    assert AutonomousConfig(stress_threshold=True).stress_threshold == 0.7
    assert AutonomousConfig(stress_threshold="0.55").stress_threshold == 0.55  # type: ignore[arg-type]
    assert AutonomousConfig(stress_threshold=10**10000).stress_threshold == 0.7
    assert AutonomousConfig(min_coverage=float("nan")).min_coverage == 0.8


def test_autonomous_config_normalizes_boolean_trigger_flags() -> None:
    enabled = AutonomousConfig(auto_refactor="true")  # type: ignore[arg-type]
    disabled = AutonomousConfig(auto_refactor="false")  # type: ignore[arg-type]
    malformed = AutonomousConfig(auto_fix_lint="later")  # type: ignore[arg-type]

    assert enabled.auto_refactor is True
    assert disabled.auto_refactor is False
    assert malformed.auto_fix_lint is True


def test_auto_enabled_requires_boolean_true() -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor.config = AutonomousConfig(auto_refactor="false")  # type: ignore[arg-type]

    assert monitor._is_auto_enabled(TriggerCondition.STRESS_THRESHOLD) is False


def test_handle_trigger_clears_pending_when_auto_action_disabled() -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor._lock = threading.Lock()
    monitor._pending_triggers = []
    monitor.graph = SimpleNamespace(_graph={})
    monitor.config = AutonomousConfig(auto_refactor=False)
    monitor._last_sweep = None

    monitor._handle_trigger(
        TriggerCondition.STRESS_THRESHOLD,
        "node-1",
        {"file_path": "src/app.py"},
    )

    assert monitor._pending_triggers == []


def test_handle_trigger_clears_pending_when_boundary_check_fails() -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor._lock = threading.Lock()
    monitor._pending_triggers = []

    def fail_boundary(component: str) -> bool:
        raise RuntimeError("boundary check failed")

    monitor._is_within_boundaries = fail_boundary

    with pytest.raises(RuntimeError, match="boundary check failed"):
        monitor._handle_trigger(
            TriggerCondition.STRESS_THRESHOLD,
            "node-1",
            {"file_path": "src/app.py"},
        )

    assert monitor._pending_triggers == []


def test_handle_trigger_clears_pending_when_auto_enabled_check_fails() -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor._lock = threading.Lock()
    monitor._pending_triggers = []
    monitor.graph = SimpleNamespace(_graph={})
    monitor.config = AutonomousConfig()
    monitor._last_sweep = None

    def fail_auto_enabled(trigger: TriggerCondition) -> bool:
        raise RuntimeError("auto-enabled check failed")

    monitor._is_auto_enabled = fail_auto_enabled

    with pytest.raises(RuntimeError, match="auto-enabled check failed"):
        monitor._handle_trigger(
            TriggerCondition.STRESS_THRESHOLD,
            "node-1",
            {"file_path": "src/app.py"},
        )

    assert monitor._pending_triggers == []


def test_handle_trigger_clears_pending_when_baseline_sweep_fails() -> None:
    class _FailingSweepEngine:
        def run(self, graph):
            raise RuntimeError("baseline sweep failed")

    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor._lock = threading.Lock()
    monitor._pending_triggers = []
    monitor.graph = SimpleNamespace(_graph={})
    monitor.config = AutonomousConfig(auto_refactor=True)
    monitor._last_sweep = None
    monitor.sweep_engine = _FailingSweepEngine()

    with pytest.raises(RuntimeError, match="baseline sweep failed"):
        monitor._handle_trigger(
            TriggerCondition.STRESS_THRESHOLD,
            "node-1",
            {"file_path": "src/app.py"},
        )

    assert monitor._pending_triggers == []


def test_handle_trigger_clears_pending_when_stress_lookup_fails() -> None:
    class _FailingStressGraph:
        _graph = {"node-1": object()}

        def impact_radius(self, component: str) -> list[str]:
            return []

        def stress_score(self, component: str) -> float:
            raise RuntimeError("stress lookup failed")

    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor._lock = threading.Lock()
    monitor._pending_triggers = []
    monitor.graph = _FailingStressGraph()
    monitor.config = AutonomousConfig(auto_refactor=True)
    monitor._last_sweep = SimpleNamespace(phase=Phase.RIDGE)

    with pytest.raises(RuntimeError, match="stress lookup failed"):
        monitor._handle_trigger(
            TriggerCondition.STRESS_THRESHOLD,
            "node-1",
            {"file_path": "src/app.py"},
        )

    assert monitor._pending_triggers == []


def test_handle_trigger_clears_pending_when_candidate_generation_fails() -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor._lock = threading.Lock()
    monitor._pending_triggers = []
    monitor.graph = SimpleNamespace(_graph={})
    monitor.config = AutonomousConfig(auto_refactor=True)
    monitor._last_sweep = SimpleNamespace(phase=Phase.RIDGE)

    def fail_generate(*args, **kwargs) -> list[dict]:
        raise RuntimeError("candidate generation failed")

    monitor._generate_candidates = fail_generate

    with pytest.raises(RuntimeError, match="candidate generation failed"):
        monitor._handle_trigger(
            TriggerCondition.STRESS_THRESHOLD,
            "node-1",
            {"file_path": "src/app.py"},
        )

    assert monitor._pending_triggers == []


def test_handle_trigger_clears_pending_when_candidate_scoring_fails() -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor._lock = threading.Lock()
    monitor._pending_triggers = []
    monitor.graph = SimpleNamespace(_graph={})
    monitor.config = AutonomousConfig(auto_refactor=True)
    monitor._last_sweep = SimpleNamespace(phase=Phase.RIDGE)
    monitor._generate_candidates = lambda *args, **kwargs: [object()]

    with pytest.raises(TypeError):
        monitor._handle_trigger(
            TriggerCondition.STRESS_THRESHOLD,
            "node-1",
            {"file_path": "src/app.py"},
        )

    assert monitor._pending_triggers == []


def test_handle_trigger_clears_pending_when_after_sweep_fails() -> None:
    class _FailingSweepEngine:
        def run(self, graph):
            raise RuntimeError("after sweep failed")

    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor._lock = threading.Lock()
    monitor._pending_triggers = []
    monitor.graph = SimpleNamespace(_graph={})
    monitor.config = AutonomousConfig(auto_refactor=True)
    monitor._last_sweep = SimpleNamespace(
        phase=Phase.RIDGE,
        kappa=0.2,
        sigma=0.3,
        es_score=0.4,
    )
    monitor.sweep_engine = _FailingSweepEngine()
    monitor._generate_candidates = lambda *args, **kwargs: [
        {"title": "Fix stress", "description": "Fix stress", "score": 1.0}
    ]

    with pytest.raises(RuntimeError, match="after sweep failed"):
        monitor._handle_trigger(
            TriggerCondition.STRESS_THRESHOLD,
            "node-1",
            {"file_path": "src/app.py"},
        )

    assert monitor._pending_triggers == []


def test_handle_trigger_clears_pending_when_audit_logging_fails() -> None:
    class _SweepEngine:
        def run(self, graph):
            return SimpleNamespace(kappa=0.3, sigma=0.4, es_score=0.5)

    class _FailingAuditDb:
        def log_action(self, **kwargs):
            raise RuntimeError("audit write failed")

    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor._lock = threading.Lock()
    monitor._pending_triggers = []
    monitor.graph = SimpleNamespace(_graph={})
    monitor.config = AutonomousConfig(auto_refactor=True)
    monitor._last_sweep = SimpleNamespace(
        phase=Phase.RIDGE,
        kappa=0.2,
        sigma=0.3,
        es_score=0.4,
    )
    monitor.sweep_engine = _SweepEngine()
    monitor.audit_db = _FailingAuditDb()
    monitor._generate_candidates = lambda *args, **kwargs: [
        {"title": "Fix stress", "description": "Fix stress", "score": 1.0}
    ]

    with pytest.raises(RuntimeError, match="audit write failed"):
        monitor._handle_trigger(
            TriggerCondition.STRESS_THRESHOLD,
            "node-1",
            {"file_path": "src/app.py"},
        )

    assert monitor._pending_triggers == []


def test_clear_pending_trigger_removes_exact_duplicate_entry() -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor._lock = threading.Lock()
    first_details = {"file_path": "src/app.py"}
    second_details = {"file_path": "src/app.py"}
    first = (TriggerCondition.STRESS_THRESHOLD, "node-1", first_details)
    second = (TriggerCondition.STRESS_THRESHOLD, "node-1", second_details)
    monitor._pending_triggers = [first, second]

    monitor._clear_pending_trigger(second)

    assert len(monitor._pending_triggers) == 1
    assert monitor._pending_triggers[0] is first


def test_is_within_boundaries_rejects_invalid_phase_constraint() -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor.graph = SimpleNamespace(_graph={})
    monitor.config = AutonomousConfig(phase_constraint="not-a-phase")
    monitor._last_sweep = None

    assert monitor._is_within_boundaries("node-1") is False


def test_is_within_boundaries_strips_phase_constraint() -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor.graph = SimpleNamespace(_graph={})
    monitor.config = AutonomousConfig(phase_constraint=" RIDGE ")
    monitor._last_sweep = SimpleNamespace(phase=Phase.RIDGE)

    assert monitor._is_within_boundaries("node-1") is True


def test_sweep_loop_redacts_failures_without_traceback(caplog) -> None:
    monitor = AutonomousMonitor.__new__(AutonomousMonitor)
    monitor.config = SimpleNamespace(sweep_interval=60)
    monitor._running = True

    def fail_sweep() -> None:
        monitor._running = False
        raise RuntimeError(
            "clone failed for https://user:secret@example.test/repo.git "
            "with https://example.test/repo.git?client_secret=client123 "
            "mirror=https://example.test/repo.git#client_secret=fragment123 "
            "access_token=abc123 auth_token=auth123 refresh_token=refresh123 "
            "password=pw123 for operator@example.test authorization=Bearer bearer123"
        )

    monitor._run_single_sweep = fail_sweep

    caplog.set_level(logging.ERROR, logger="codey.autonomous.monitor")

    monitor._sweep_loop()

    assert "user:secret" not in caplog.text
    assert "secret@example.test" not in caplog.text
    assert "client123" not in caplog.text
    assert "fragment123" not in caplog.text
    assert "abc123" not in caplog.text
    assert "auth123" not in caplog.text
    assert "refresh123" not in caplog.text
    assert "pw123" not in caplog.text
    assert "bearer123" not in caplog.text
    assert "operator@example.test" not in caplog.text
    assert "https://***@example.test/repo.git" in caplog.text
    assert "client_secret=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "auth_token=***" in caplog.text
    assert "refresh_token=***" in caplog.text
    assert "password=***" in caplog.text
    assert "***@example.test" in caplog.text
    assert "authorization=Bearer ***" in caplog.text
    assert "Traceback" not in caplog.text
