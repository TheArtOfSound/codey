from __future__ import annotations

import json
import logging
import uuid
from types import SimpleNamespace

import pytest

from codey.saas.vault.service import VaultService


class _FakeDB:
    def add(self, _obj) -> None:
        return None

    async def flush(self) -> None:
        return None


class _CreateVersionResult:
    def __init__(self, current_max=0) -> None:
        self._current_max = current_max

    def scalar_one(self):
        return self._current_max


class _Unserializable:
    def __str__(self) -> str:
        return "fallback-content"


def test_vault_stringify_content_sanitizes_non_finite_json() -> None:
    raw = VaultService._stringify_vault_content({
        "stress": float("inf"),
        "nested": (float("nan"),),
        "set_metric": {float("inf")},
    })

    payload = json.loads(raw)

    assert payload == {
        "stress": 0.0,
        "nested": [0.0],
        "set_metric": [0.0],
    }
    json.dumps(payload, allow_nan=False)


def test_vault_stringify_content_serializes_nested_non_json_edge_values() -> None:
    raw = VaultService._stringify_vault_content(
        {
            ("tuple", "key"): b"vault-bytes",
            "set_values": {"b", "a"},
            "nested": {"opaque": _Unserializable()},
        }
    )

    payload = json.loads(raw)

    assert payload == {
        "('tuple', 'key')": "vault-bytes",
        "set_values": ["a", "b"],
        "nested": {"opaque": "fallback-content"},
    }
    assert VaultService._stringify_vault_content(_Unserializable()) == "fallback-content"
    json.dumps(payload, allow_nan=False)


def test_vault_stringify_content_sanitizes_cyclic_json_payloads() -> None:
    cycle: dict[str, object] = {"type": "snapshot"}
    cycle["self"] = cycle

    raw = VaultService._stringify_vault_content(cycle)

    assert json.loads(raw) == {
        "type": "snapshot",
        "self": "[Circular]",
    }


def test_vault_service_row_list_coercion_rejects_malformed_results() -> None:
    row = SimpleNamespace(id="row-1")

    assert VaultService._coerce_vault_row_list([row]) == [row]
    assert VaultService._coerce_vault_row_list((row,)) == [row]
    assert VaultService._coerce_vault_row_list(None) == []
    assert VaultService._coerce_vault_row_list("bad") == []


class _CreateVersionDB(_FakeDB):
    def __init__(self, project, *, current_max=0) -> None:
        self.project = project
        self.current_max = current_max
        self.flush_calls = 0

    async def execute(self, _statement):
        return _CreateVersionResult(self.current_max)

    async def get(self, _model, _id):
        return self.project

    async def flush(self) -> None:
        self.flush_calls += 1


@pytest.mark.asyncio
async def test_export_project_marks_empty_zip_exports_failed(monkeypatch) -> None:
    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, name="demo")
    version = SimpleNamespace(file_snapshot={})
    service = VaultService(_FakeDB())

    async def fake_get_owned_project(self, user_id_arg, project_id_arg):
        assert user_id_arg == user_id
        assert project_id_arg == project_id
        return project

    async def fake_get_project_versions(self, project_id_arg):
        assert project_id_arg == project_id
        return [version]

    monkeypatch.setattr(VaultService, "_get_owned_project", fake_get_owned_project)
    monkeypatch.setattr(VaultService, "get_project_versions", fake_get_project_versions)

    export_record = await service.export_project(
        user_id,
        project_id,
        "zip",
        destination="demo.zip",
    )

    assert export_record.status == "failed"
    assert export_record.error_message == "No project files available for export"
    assert export_record.completed_at is None
    assert export_record.file_size_bytes is None
    assert export_record.metadata_ is None


@pytest.mark.asyncio
async def test_export_project_persists_redacted_error_messages(
    caplog,
    monkeypatch,
) -> None:
    user_id = uuid.uuid4()
    project_id = "https://project-user:secret@example.test/project?token=project-token"
    project = SimpleNamespace(id=project_id, name="demo")
    service = VaultService(_FakeDB())

    async def fake_get_owned_project(self, user_id_arg, project_id_arg):
        assert user_id_arg == user_id
        assert project_id_arg == project_id
        return project

    async def fail_export_zip(self, project_arg, export_record):
        assert project_arg == project
        raise RuntimeError(
            "export failed https://user:secret@example.test/repo?token=abc "
            "for operator@example.test api_key=abc "
            "client_secret=client-secret password=inline-password "
            "artifact=export#client_secret=fragment-secret "
            "authorization=Bearer bearer-secret",
        )

    monkeypatch.setattr(VaultService, "_get_owned_project", fake_get_owned_project)
    monkeypatch.setattr(VaultService, "_export_zip", fail_export_zip)
    caplog.set_level(logging.ERROR, logger="codey.saas.vault.service")

    export_record = await service.export_project(
        user_id,
        project_id,
        "zip",
        destination="demo.zip",
    )

    assert export_record.status == "failed"
    assert "https://user:secret@" not in export_record.error_message
    assert "client-secret" not in export_record.error_message
    assert "fragment-secret" not in export_record.error_message
    assert "abc" not in export_record.error_message
    assert "inline-password" not in export_record.error_message
    assert "bearer-secret" not in export_record.error_message
    assert "operator@example.test" not in export_record.error_message
    assert "https://example.test/repo?redacted=***" in export_record.error_message
    assert "api_key=***" in export_record.error_message
    assert "client_secret=***" in export_record.error_message
    assert "password=***" in export_record.error_message
    assert "authorization=Bearer ***" in export_record.error_message
    assert "***@example.test" in export_record.error_message
    assert "https://project-user:secret@" not in caplog.text
    assert "https://user:secret@" not in caplog.text
    assert "client-secret" not in caplog.text
    assert "project-token" not in caplog.text
    assert "abc" not in caplog.text
    assert "operator@example.test" not in caplog.text
    assert "https://example.test/project?redacted=***" in caplog.text
    assert "https://example.test/repo?redacted=***" in caplog.text


@pytest.mark.asyncio
async def test_export_json_tolerates_string_version_created_at(monkeypatch) -> None:
    project_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id,
        name="demo",
        language="python",
        framework="fastapi",
    )
    version = SimpleNamespace(
        version_number=1,
        commit_message="Initial snapshot",
        files_changed=["app.py"],
        nfet_phase="build",
        es_score=0.8,
        created_at=" 2026-01-02T03:04:05Z ",
    )
    export_record = SimpleNamespace(file_size_bytes=None, metadata_=None)
    service = VaultService(_FakeDB())

    async def fake_get_project_versions(self, project_id_arg):
        assert project_id_arg == project_id
        return [version]

    monkeypatch.setattr(VaultService, "get_project_versions", fake_get_project_versions)

    await service._export_json(project, export_record)

    assert export_record.metadata_ == {
        "format": "json",
        "version_count": 1,
        "project_name": "demo",
    }

    expected_payload = {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "language": project.language,
            "framework": project.framework,
        },
        "versions": [
            {
                "version_number": version.version_number,
                "commit_message": version.commit_message,
                "files_changed": version.files_changed,
                "nfet_phase": version.nfet_phase,
                "es_score": version.es_score,
                "created_at": "2026-01-02T03:04:05Z",
            }
        ],
    }
    expected_raw = json.dumps(expected_payload, indent=2)
    assert export_record.file_size_bytes == len(expected_raw.encode())


@pytest.mark.asyncio
async def test_export_json_stringifies_unserializable_version_fields(
    monkeypatch,
) -> None:
    project_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id,
        name="demo",
        language="python",
        framework="fastapi",
    )
    version = SimpleNamespace(
        version_number=1,
        commit_message="Initial snapshot",
        files_changed=_Unserializable(),
        nfet_phase="build",
        es_score=0.8,
        created_at=None,
    )
    export_record = SimpleNamespace(file_size_bytes=None, metadata_=None)
    service = VaultService(_FakeDB())

    async def fake_get_project_versions(self, project_id_arg):
        assert project_id_arg == project_id
        return [version]

    monkeypatch.setattr(VaultService, "get_project_versions", fake_get_project_versions)

    await service._export_json(project, export_record)

    expected_payload = {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "language": project.language,
            "framework": project.framework,
        },
        "versions": [
            {
                "version_number": version.version_number,
                "commit_message": version.commit_message,
                "files_changed": "fallback-content",
                "nfet_phase": version.nfet_phase,
                "es_score": version.es_score,
                "created_at": None,
            }
        ],
    }
    expected_raw = json.dumps(expected_payload, indent=2)
    assert export_record.file_size_bytes == len(expected_raw.encode())
    assert export_record.metadata_ == {
        "format": "json",
        "version_count": 1,
        "project_name": "demo",
    }


@pytest.mark.asyncio
async def test_create_version_tolerates_non_dict_nfet_state() -> None:
    project = SimpleNamespace(
        total_versions=0,
        total_sessions=0,
        last_activity=None,
        latest_nfet_phase="ridge",
        latest_es_score=0.42,
    )
    db = _CreateVersionDB(project)
    service = VaultService(db)

    version = await service.create_version(
        project_id=uuid.uuid4(),
        session_id=None,
        files_changed=None,
        diff=None,
        commit_message="snapshot",
        nfet_state="oops",
    )

    assert version.nfet_state is None
    assert version.nfet_phase is None
    assert version.es_score is None
    assert project.total_versions == 1
    assert project.total_sessions == 0
    assert project.last_activity is not None
    assert project.latest_nfet_phase == "ridge"
    assert project.latest_es_score == 0.42


@pytest.mark.asyncio
async def test_create_version_tolerates_malformed_current_max_version() -> None:
    project = SimpleNamespace(
        total_versions=0,
        total_sessions=0,
        last_activity=None,
        latest_nfet_phase=None,
        latest_es_score=None,
    )
    db = _CreateVersionDB(project, current_max="bad-version")
    service = VaultService(db)

    version = await service.create_version(
        project_id=uuid.uuid4(),
        session_id=None,
        files_changed=None,
        diff=None,
        commit_message="snapshot",
        nfet_state=None,
    )

    assert version.version_number == 1
    assert project.total_versions == 1
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_export_project_marks_invalid_zip_snapshots_failed(monkeypatch) -> None:
    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, name="demo")
    version = SimpleNamespace(file_snapshot="oops")
    service = VaultService(_FakeDB())

    async def fake_get_owned_project(self, user_id_arg, project_id_arg):
        assert user_id_arg == user_id
        assert project_id_arg == project_id
        return project

    async def fake_get_project_versions(self, project_id_arg):
        assert project_id_arg == project_id
        return [version]

    monkeypatch.setattr(VaultService, "_get_owned_project", fake_get_owned_project)
    monkeypatch.setattr(VaultService, "get_project_versions", fake_get_project_versions)

    export_record = await service.export_project(
        user_id,
        project_id,
        "zip",
        destination="demo.zip",
    )

    assert export_record.status == "failed"
    assert export_record.error_message == "No project files available for export"
    assert export_record.completed_at is None


@pytest.mark.asyncio
async def test_export_project_zip_stringifies_unserializable_snapshot_content(
    monkeypatch,
) -> None:
    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, name="demo")
    version = SimpleNamespace(file_snapshot={"notes.txt": _Unserializable()})
    service = VaultService(_FakeDB())

    async def fake_get_owned_project(self, user_id_arg, project_id_arg):
        assert user_id_arg == user_id
        assert project_id_arg == project_id
        return project

    async def fake_get_project_versions(self, project_id_arg):
        assert project_id_arg == project_id
        return [version]

    monkeypatch.setattr(VaultService, "_get_owned_project", fake_get_owned_project)
    monkeypatch.setattr(VaultService, "get_project_versions", fake_get_project_versions)

    export_record = await service.export_project(
        user_id,
        project_id,
        "zip",
        destination="demo.zip",
    )

    assert export_record.status == "completed"
    assert export_record.error_message is None
    assert export_record.file_size_bytes is not None
    assert export_record.metadata_ == {
        "format": "zip",
        "file_count": 1,
        "project_name": "demo",
    }
