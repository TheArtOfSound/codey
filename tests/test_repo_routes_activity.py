from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, status

import codey.saas.api.repo_routes as repo_routes


class _FlushTrackerDB:
    def __init__(self) -> None:
        self.flushed = False

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_get_repo_activity_skips_malformed_entries(monkeypatch) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(
            id="repo-1",
            autonomous_config={
                "activity_log": [
                    "bad-entry",
                    {"action": "  sync  ", "timestamp": 123, "details": "not-a-dict"},
                    {
                        "action": "deploy",
                        "timestamp": "2025-01-01T00:00:00Z",
                        "details": {"result": "ok"},
                    },
                    {"action": "   ", "timestamp": "", "details": None},
                ]
            },
        )

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    response = await repo_routes.get_repo_activity(
        "repo-id",
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert [entry.action for entry in response.entries] == ["sync", "deploy", "unknown"]
    assert [entry.timestamp for entry in response.entries] == [
        "123",
        "2025-01-01T00:00:00Z",
        "",
    ]
    assert response.entries[0].details is None
    assert response.entries[1].details == {"result": "ok"}


@pytest.mark.asyncio
async def test_get_repo_activity_tolerates_non_dict_autonomous_config(monkeypatch) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(
            id="repo-1",
            autonomous_config="corrupt-config",
        )

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    response = await repo_routes.get_repo_activity(
        "repo-id",
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert response.entries == []


@pytest.mark.asyncio
async def test_get_repo_activity_tolerates_missing_autonomous_config(monkeypatch) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(id="repo-1")

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    response = await repo_routes.get_repo_activity(
        "repo-id",
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert response.entries == []


def test_repo_to_response_tolerates_non_dict_autonomous_config() -> None:
    repo = SimpleNamespace(
        id="repo-1",
        full_name="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        default_branch="main",
        language="python",
        autonomous_mode_enabled=True,
        autonomous_config=["corrupt-config"],
        last_analyzed=None,
        nfet_phase="observe",
        es_score=0.42,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

    response = repo_routes._repo_to_response(repo)

    assert response.id == "repo-1"
    assert response.autonomous_config is None


def test_repo_to_response_tolerates_non_string_text_fields() -> None:
    repo = SimpleNamespace(
        id="repo-1",
        full_name=["owner/repo"],
        clone_url={"url": "https://github.com/owner/repo.git"},
        default_branch=["main"],
        language={"name": "python"},
        autonomous_mode_enabled=True,
        autonomous_config={},
        last_analyzed=None,
        nfet_phase="observe",
        es_score=0.42,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

    response = repo_routes._repo_to_response(repo)

    assert response.full_name is None
    assert response.clone_url is None
    assert response.default_branch is None
    assert response.language is None


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://github.com/owner/repo.git\r\nbad",
        "https://github.com/owner/repo .git",
        "git@github.com:owner/repo .git",
    ],
)
def test_repo_to_response_rejects_malformed_clone_url_text(clone_url: str) -> None:
    repo = SimpleNamespace(
        id="repo-1",
        full_name="owner/repo",
        clone_url=clone_url,
        default_branch="main",
        language="python",
        autonomous_mode_enabled=True,
        autonomous_config={},
        last_analyzed=None,
        nfet_phase="observe",
        es_score=0.42,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

    response = repo_routes._repo_to_response(repo)

    assert response.clone_url is None


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://github.com/owner/repo.git?access_token=secret",
        "https://github.com/owner/repo.git#readme",
        "https://user:secret@github.com/owner/repo.git",
        "ssh://git:secret@github.com/owner/repo.git",
        "ssh://root@github.com/owner/repo.git",
        "https://github.com:not-a-port/owner/repo.git",
        "https:///owner/repo.git",
        "owner/repo",
        "/tmp/repo.git",
        "github.com:owner/repo.git",
        "git@gitlab.com:owner/repo.git",
        "file:///tmp/repo.git",
        "ftp://github.com/owner/repo.git",
        "javascript://github.com/owner/repo.git",
    ],
)
def test_repo_to_response_rejects_unsafe_clone_url_shapes(clone_url: str) -> None:
    repo = SimpleNamespace(
        id="repo-1",
        full_name="owner/repo",
        clone_url=clone_url,
        default_branch="main",
        language="python",
        autonomous_mode_enabled=True,
        autonomous_config={},
        last_analyzed=None,
        nfet_phase="observe",
        es_score=0.42,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

    response = repo_routes._repo_to_response(repo)

    assert response.clone_url is None


@pytest.mark.parametrize(
    "clone_url",
    [
        "git@github.com:owner/repo.git",
        "ssh://git@github.com/owner/repo.git",
    ],
)
def test_repo_to_response_accepts_safe_ssh_clone_urls(clone_url: str) -> None:
    repo = SimpleNamespace(
        id="repo-1",
        full_name="owner/repo",
        clone_url=clone_url,
        default_branch="main",
        language="python",
        autonomous_mode_enabled=True,
        autonomous_config={},
        last_analyzed=None,
        nfet_phase="observe",
        es_score=0.42,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

    response = repo_routes._repo_to_response(repo)

    assert response.clone_url == clone_url


def test_repo_to_response_tolerates_missing_optional_fields() -> None:
    repo = SimpleNamespace(id="repo-1")

    response = repo_routes._repo_to_response(repo)

    assert response.id == "repo-1"
    assert response.full_name is None
    assert response.clone_url is None
    assert response.default_branch is None
    assert response.language is None
    assert response.autonomous_mode_enabled is False
    assert response.autonomous_config is None
    assert response.last_analyzed is None
    assert response.nfet_phase is None
    assert response.es_score == 0.0
    assert response.created_at == ""


def test_repo_to_response_tolerates_string_timestamps() -> None:
    repo = SimpleNamespace(
        id="repo-1",
        full_name="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        default_branch="main",
        language="python",
        autonomous_mode_enabled=True,
        autonomous_config={},
        last_analyzed=" 2025-01-02T00:00:00Z ",
        nfet_phase="observe",
        es_score=0.42,
        created_at="2025-01-01T00:00:00Z",
    )

    response = repo_routes._repo_to_response(repo)

    assert response.created_at == "2025-01-01T00:00:00Z"
    assert response.last_analyzed == "2025-01-02T00:00:00Z"


def test_repo_to_response_normalizes_malformed_state_fields() -> None:
    repo = SimpleNamespace(
        id="repo-1",
        full_name="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        default_branch="main",
        language="python",
        autonomous_mode_enabled={"enabled": True},
        autonomous_config={},
        last_analyzed=None,
        nfet_phase=["observe"],
        es_score=["0.42"],
        created_at="2025-01-01T00:00:00Z",
    )

    response = repo_routes._repo_to_response(repo)

    assert response.autonomous_mode_enabled is False
    assert response.nfet_phase is None
    assert response.es_score == 0.0


def test_repo_float_coercion_rejects_non_finite_values() -> None:
    assert repo_routes._coerce_repo_float(float("nan"), fallback=-1.0) == -1.0
    assert repo_routes._coerce_repo_float(float("inf"), fallback=-1.0) == -1.0
    assert repo_routes._coerce_repo_float("-inf", fallback=-1.0) == -1.0
    assert repo_routes._coerce_repo_float("0.42", fallback=-1.0) == 0.42


def test_repo_int_coercion_rejects_non_finite_values() -> None:
    assert repo_routes._coerce_repo_int(float("nan"), fallback=-1) == -1
    assert repo_routes._coerce_repo_int(float("inf"), fallback=-1) == -1
    assert repo_routes._coerce_repo_int("-inf", fallback=-1) == -1
    assert repo_routes._coerce_repo_int("3", fallback=-1) == 3
    assert repo_routes._coerce_optional_repo_int(float("nan")) is None
    assert repo_routes._coerce_optional_repo_int("inf") is None
    assert repo_routes._coerce_optional_repo_int("123") == 123


def test_repo_bool_coercion_rejects_non_finite_values() -> None:
    assert repo_routes._coerce_repo_bool(float("nan"), fallback=False) is False
    assert repo_routes._coerce_repo_bool(float("inf"), fallback=False) is False
    assert repo_routes._coerce_repo_bool(1, fallback=False) is True
    assert repo_routes._coerce_repo_bool("yes", fallback=False) is True


def test_repo_row_list_coercion_rejects_malformed_results() -> None:
    row = SimpleNamespace(id="row-1")

    assert repo_routes._coerce_repo_row_list([row]) == [row]
    assert repo_routes._coerce_repo_row_list((row,)) == [row]
    assert repo_routes._coerce_repo_row_list(None) == []
    assert repo_routes._coerce_repo_row_list("bad") == []


@pytest.mark.asyncio
async def test_get_repo_health_tolerates_string_last_analyzed(monkeypatch) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(
            id="repo-1",
            full_name="owner/repo",
            nfet_phase="observe",
            es_score=0.42,
            last_analyzed=" 2025-01-02T00:00:00Z ",
            autonomous_mode_enabled=True,
        )

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    response = await repo_routes.get_repo_health(
        "repo-id",
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert response.last_analyzed == "2025-01-02T00:00:00Z"


@pytest.mark.asyncio
async def test_get_repo_health_normalizes_malformed_state_fields(monkeypatch) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(
            id="repo-1",
            full_name="owner/repo",
            nfet_phase=["observe"],
            es_score=["0.42"],
            last_analyzed=None,
            autonomous_mode_enabled={"enabled": True},
        )

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    response = await repo_routes.get_repo_health(
        "repo-id",
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert response.nfet_phase is None
    assert response.es_score == 0.0
    assert response.autonomous_mode_enabled is False


@pytest.mark.asyncio
async def test_get_repo_health_tolerates_missing_optional_fields(monkeypatch) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(id="repo-1")

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    response = await repo_routes.get_repo_health(
        "repo-id",
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert response.full_name is None
    assert response.nfet_phase is None
    assert response.es_score == 0.0
    assert response.last_analyzed is None
    assert response.autonomous_mode_enabled is False


@pytest.mark.asyncio
async def test_toggle_autonomous_mode_rejects_missing_clone_url_when_enabling(
    monkeypatch,
) -> None:
    repo = SimpleNamespace(
        clone_url={"href": "https://github.com/owner/repo.git"},
        autonomous_mode_enabled=False,
        autonomous_config={},
    )
    db = _FlushTrackerDB()

    async def fake_load_owned_repo(repo_id, current_user, db):
        return repo

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.toggle_autonomous_mode(
            "repo-id",
            body=repo_routes.AutonomousModeRequest(enabled=True),
            current_user=SimpleNamespace(id="user-1", is_pro_or_above=True),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no clone URL configured"
    assert repo.autonomous_mode_enabled is False
    assert db.flushed is False


@pytest.mark.asyncio
async def test_toggle_autonomous_mode_rejects_absent_clone_url_when_enabling(
    monkeypatch,
) -> None:
    repo = SimpleNamespace(
        autonomous_mode_enabled=False,
        autonomous_config={},
    )
    db = _FlushTrackerDB()

    async def fake_load_owned_repo(repo_id, current_user, db):
        return repo

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.toggle_autonomous_mode(
            "repo-id",
            body=repo_routes.AutonomousModeRequest(enabled=True),
            current_user=SimpleNamespace(id="user-1", is_pro_or_above=True),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no clone URL configured"
    assert repo.autonomous_mode_enabled is False
    assert db.flushed is False


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://github.com/owner/repo.git\r\nbad",
        "https://github.com/owner/repo .git",
        "git@github.com:owner/repo .git",
    ],
)
@pytest.mark.asyncio
async def test_toggle_autonomous_mode_rejects_malformed_clone_url_text(
    monkeypatch,
    clone_url: str,
) -> None:
    repo = SimpleNamespace(
        clone_url=clone_url,
        autonomous_mode_enabled=False,
        autonomous_config={},
    )
    db = _FlushTrackerDB()

    async def fake_load_owned_repo(repo_id, current_user, db):
        return repo

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.toggle_autonomous_mode(
            "repo-id",
            body=repo_routes.AutonomousModeRequest(enabled=True),
            current_user=SimpleNamespace(id="user-1", is_pro_or_above=True),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no clone URL configured"
    assert repo.autonomous_mode_enabled is False
    assert db.flushed is False


@pytest.mark.asyncio
async def test_toggle_autonomous_mode_rejects_invalid_clone_url(
    monkeypatch,
) -> None:
    repo = SimpleNamespace(
        clone_url="https://github.com:not-a-port/owner/repo.git",
        autonomous_mode_enabled=False,
        autonomous_config={},
    )
    db = _FlushTrackerDB()

    async def fake_load_owned_repo(repo_id, current_user, db):
        return repo

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.toggle_autonomous_mode(
            "repo-id",
            body=repo_routes.AutonomousModeRequest(enabled=True),
            current_user=SimpleNamespace(id="user-1", is_pro_or_above=True),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has invalid clone URL configured"
    assert repo.autonomous_mode_enabled is False
    assert db.flushed is False


@pytest.mark.parametrize(
    "clone_url",
    [
        "https://github.com/owner/repo.git?access_token=secret",
        "https://github.com/owner/repo.git#readme",
        "https://user:secret@github.com/owner/repo.git",
        "ssh://git:secret@github.com/owner/repo.git",
        "ssh://root@github.com/owner/repo.git",
        "owner/repo",
        "/tmp/repo.git",
        "github.com:owner/repo.git",
        "git@gitlab.com:owner/repo.git",
        "file:///tmp/repo.git",
        "ftp://github.com/owner/repo.git",
        "javascript://github.com/owner/repo.git",
    ],
)
@pytest.mark.asyncio
async def test_toggle_autonomous_mode_rejects_unsafe_clone_url_shapes(
    monkeypatch,
    clone_url: str,
) -> None:
    repo = SimpleNamespace(
        full_name="owner/repo",
        clone_url=clone_url,
        autonomous_mode_enabled=False,
        autonomous_config={},
    )
    db = _FlushTrackerDB()

    async def fake_load_owned_repo(repo_id, current_user, db):
        return repo

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.toggle_autonomous_mode(
            "repo-id",
            body=repo_routes.AutonomousModeRequest(enabled=True),
            current_user=SimpleNamespace(id="user-1", is_pro_or_above=True),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has invalid clone URL configured"
    assert repo.autonomous_mode_enabled is False
    assert db.flushed is False


@pytest.mark.asyncio
async def test_toggle_autonomous_mode_rejects_mismatched_clone_url(
    monkeypatch,
) -> None:
    repo = SimpleNamespace(
        full_name="owner/repo",
        clone_url="https://github.com/other/repo.git",
        autonomous_mode_enabled=False,
        autonomous_config={},
    )
    db = _FlushTrackerDB()

    async def fake_load_owned_repo(repo_id, current_user, db):
        return repo

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.toggle_autonomous_mode(
            "repo-id",
            body=repo_routes.AutonomousModeRequest(enabled=True),
            current_user=SimpleNamespace(id="user-1", is_pro_or_above=True),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository clone URL does not match repository"
    assert repo.autonomous_mode_enabled is False
    assert db.flushed is False


@pytest.mark.asyncio
async def test_toggle_autonomous_mode_rejects_user_without_pro_flag(
    monkeypatch,
) -> None:
    repo = SimpleNamespace(
        clone_url="https://github.com/owner/repo.git",
        autonomous_mode_enabled=False,
        autonomous_config={},
    )
    db = _FlushTrackerDB()

    async def fake_load_owned_repo(repo_id, current_user, db):
        return repo

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes.toggle_autonomous_mode(
            "repo-id",
            body=repo_routes.AutonomousModeRequest(enabled=True),
            current_user=SimpleNamespace(id="user-1"),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "Autonomous mode requires the Pro plan or above"
    assert repo.autonomous_mode_enabled is False
    assert db.flushed is False


@pytest.mark.asyncio
async def test_get_repo_nfet_summary_tolerates_non_string_full_name(monkeypatch) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(
            id="repo-1",
            full_name=["owner/repo"],
        )

    async def fake_analyze_repo_nfet(repo, token, goal=None, target_file=None):
        repo_state = SimpleNamespace(
            phase="observe",
            global_kappa=0.3,
            global_sigma=0.4,
            global_es=0.5,
            total_nodes=8,
            total_edges=12,
            highest_stress_component="module.py",
            highest_stress_value=0.9,
            hotspots=[],
        )
        return object(), object(), repo_state

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)
    monkeypatch.setattr(repo_routes, "_analyze_repo_nfet", fake_analyze_repo_nfet)

    response = await repo_routes.get_repo_nfet_summary(
        "repo-id",
        current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert response.full_name is None
    assert response.hotspot_count == 0


@pytest.mark.asyncio
async def test_get_repo_nfet_summary_tolerates_missing_full_name(monkeypatch) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(id="repo-1")

    async def fake_analyze_repo_nfet(repo, token, goal=None, target_file=None):
        repo_state = SimpleNamespace(
            phase="observe",
            global_kappa=0.3,
            global_sigma=0.4,
            global_es=0.5,
            total_nodes=8,
            total_edges=12,
            highest_stress_component="module.py",
            highest_stress_value=0.9,
            hotspots=[],
        )
        return object(), object(), repo_state

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)
    monkeypatch.setattr(repo_routes, "_analyze_repo_nfet", fake_analyze_repo_nfet)

    response = await repo_routes.get_repo_nfet_summary(
        "repo-id",
        current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert response.full_name is None
    assert response.hotspot_count == 0
