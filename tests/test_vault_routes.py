from __future__ import annotations

import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, status

import codey.saas.api.vault_routes as vault_routes
from codey.saas.config import settings


class _ScalarResult:
    def __init__(self, obj) -> None:
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeDB:
    def __init__(self, version, project) -> None:
        self._version = version
        self._project = project

    async def execute(self, _statement):
        return _ScalarResult(self._version)

    async def get(self, model, _id):
        return self._project

    async def flush(self) -> None:
        return None


def _make_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "headers": headers or [],
        }
    )


@pytest.mark.asyncio
async def test_restore_project_version_maps_service_value_errors_to_not_found(
    monkeypatch,
) -> None:
    project_id = uuid.uuid4()
    version_id = uuid.uuid4()
    version = SimpleNamespace(id=version_id, version_number=3)
    project = SimpleNamespace(user_id="user-1", file_tree={"main.py": "print('hi')"})
    db = _FakeDB(version, project)

    class _FailingVaultService:
        def __init__(self, db_session) -> None:
            self.db = db_session

        async def restore_version(self, project_uuid, version_number):
            raise ValueError("version missing")

    monkeypatch.setattr(vault_routes, "VaultService", _FailingVaultService)

    with pytest.raises(HTTPException) as exc_info:
        await vault_routes.restore_project_version(
            str(project_id),
            vault_routes.RestoreVersionRequest(version_id=str(version_id)),
            current_user=SimpleNamespace(id="user-1"),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "Version not found"


def test_restore_to_response_normalizes_malformed_version_numbers() -> None:
    restored = SimpleNamespace(
        id=uuid.uuid4(),
        version_number=["7"],
    )

    response = vault_routes._restore_to_response(restored, " 3 ")

    assert response.version_id == str(restored.id)
    assert response.version_number == 0
    assert response.message == "Project restored to version 3"


def test_vault_numeric_coercion_rejects_non_finite_values() -> None:
    assert vault_routes._coerce_vault_int(float("nan"), fallback=-1) == -1
    assert vault_routes._coerce_vault_int(float("inf"), fallback=-1) == -1
    assert vault_routes._coerce_vault_int("-inf", fallback=-1) == -1
    assert vault_routes._coerce_vault_int("3", fallback=-1) == 3
    assert vault_routes._coerce_vault_float(float("nan")) is None
    assert vault_routes._coerce_vault_float("inf") is None
    assert vault_routes._coerce_vault_float("0.42") == 0.42


def test_vault_row_list_coercion_rejects_malformed_results() -> None:
    row = SimpleNamespace(id="row-1")

    assert vault_routes._coerce_vault_row_list([row]) == [row]
    assert vault_routes._coerce_vault_row_list((row,)) == [row]
    assert vault_routes._coerce_vault_row_list(None) == []
    assert vault_routes._coerce_vault_row_list("bad") == []


def test_stringify_vault_content_sanitizes_non_finite_json() -> None:
    raw = vault_routes._stringify_vault_content({
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


def test_count_vault_content_lines_ignores_blank_and_trailing_lines() -> None:
    assert vault_routes._count_vault_content_lines("") == 0
    assert vault_routes._count_vault_content_lines("print('ok')\n") == 1
    assert (
        vault_routes._count_vault_content_lines("\nprint('one')\n\nprint('two')\n")
        == 2
    )


def test_build_vault_webhook_payload_sanitizes_snapshot_json() -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        name="demo",
        language="python",
        framework="fastapi",
    )
    version = SimpleNamespace(
        version_number=1,
        commit_message="snapshot",
        created_at=None,
        file_snapshot={
            "stress": float("inf"),
            "nested": (float("nan"),),
            "set_metric": {float("inf")},
        },
    )

    payload = vault_routes._build_vault_webhook_payload(project, [version])

    assert payload["versions"][0]["file_snapshot"] == {
        "stress": 0.0,
        "nested": [0.0],
        "set_metric": [0.0],
    }
    json.dumps(payload, allow_nan=False)


def test_vault_project_name_map_skips_malformed_rows() -> None:
    project_id = uuid.uuid4()

    assert vault_routes._vault_project_name_map(
        [
            SimpleNamespace(id=project_id, name="demo"),
            SimpleNamespace(name="missing-id"),
            "bad-row",
        ]
    ) == {project_id: "demo"}


def test_normalize_vault_webhook_url_accepts_https_and_strips_fragments() -> None:
    assert (
        vault_routes._normalize_vault_webhook_url(
            " https://hooks.example.com/builds?token=abc#secret "
        )
        == "https://hooks.example.com/builds?token=abc"
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://hooks.example.com/builds",
        "https://user:pass@hooks.example.com/builds",
        "https://hooks.example.com:not-a-port/builds",
        "https://hooks.example.com:0/builds",
        "https://hooks.example.com/builds?token=abc\tdef",
        "http://localhost/builds",
        "http://service.localhost/builds",
        "http://127.0.0.1/builds",
        "http://2130706433/builds",
        "http://0x7f000001/builds",
        "http://0177.0.0.1/builds",
        "http://10.0.0.5/builds",
        "http://192.168.1.10/builds",
        "http://3232235786/builds",
        "http://0300.0250.0001.0012/builds",
        "http://169.254.169.254/latest/meta-data",
        "http://2852039166/latest/meta-data",
        "http://0xa9fea9fe/latest/meta-data",
        "http://[::1]/builds",
        "hooks.example.com/builds",
    ],
)
def test_normalize_vault_webhook_url_rejects_unsafe_urls(url: str) -> None:
    assert vault_routes._normalize_vault_webhook_url(url) is None


def test_redact_vault_error_strips_webhook_query_and_userinfo() -> None:
    message = vault_routes._redact_vault_error(
        "delivery failed for "
        "https://user:pass@hooks.example.com/builds?token=url-secret#fragment "
        "api_key=key123 auth_token=auth123 refresh_token=refresh123 "
        "client_secret=client123 password=pw123 authorization=Bearer bearer123 "
        "for operator@example.test"
    )

    assert "user:pass" not in message
    assert "url-secret" not in message
    assert "fragment" not in message
    assert "key123" not in message
    assert "auth123" not in message
    assert "refresh123" not in message
    assert "client123" not in message
    assert "pw123" not in message
    assert "bearer123" not in message
    assert "operator@example.test" not in message
    assert "https://hooks.example.com/builds?redacted=***" in message
    assert "api_key=***" in message
    assert "auth_token=***" in message
    assert "refresh_token=***" in message
    assert "client_secret=***" in message
    assert "password=***" in message
    assert "authorization=Bearer ***" in message
    assert "***@example.test" in message


def test_vault_export_download_filename_sanitizes_header_unsafe_destination() -> None:
    filename = vault_routes._vault_export_download_filename(
        'reports/"demo"\r\nx.zip',
        "export-1",
    )

    assert filename == "demo_x.zip"
    assert '"' not in filename
    assert "\r" not in filename
    assert "\n" not in filename


def test_vault_export_download_filename_uses_zip_fallback_for_blank_destination() -> None:
    filename = vault_routes._vault_export_download_filename("   ", "export-1")

    assert filename == "codey-export-export-1.zip"


class _CreateExportDB:
    def __init__(self, project) -> None:
        self._project = project

    async def get(self, model, _id):
        return self._project


class _ListResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _ListExportsDB:
    async def execute(self, _statement):
        return _ListResult()


class _ListProjectsResult:
    def __init__(self, projects) -> None:
        self._projects = projects

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        return self._projects


class _ListProjectsDB:
    def __init__(self, projects) -> None:
        self._projects = projects

    async def execute(self, _statement):
        return _ListProjectsResult(self._projects)


class _DownloadExportDB:
    def __init__(self, export_record) -> None:
        self._export_record = export_record

    async def get(self, model, _id):
        return self._export_record


def test_summarize_version_tolerates_string_created_at() -> None:
    version = SimpleNamespace(
        id=uuid.uuid4(),
        version_number=3,
        created_at=" 2026-01-02T03:04:05Z ",
        es_score=0.88,
        commit_message="Refine export flow",
        diff=None,
        file_snapshot={},
        files_changed=[],
    )

    response = vault_routes._summarize_version(version)

    assert response.created_at == "2026-01-02T03:04:05Z"


def test_snapshot_to_tree_ignores_invalid_snapshot_shapes() -> None:
    assert vault_routes._snapshot_to_tree("oops") == []


def test_snapshot_to_tree_ignores_empty_snapshot_paths() -> None:
    tree = vault_routes._snapshot_to_tree({"": "blank", "/": "root"})

    assert tree == []


def test_snapshot_to_tree_tolerates_file_directory_conflicts() -> None:
    for snapshot in (
        {"src": "file-content", "src/app.py": "print('hi')"},
        {"src/app.py": "print('hi')", "src": "file-content"},
    ):
        tree = vault_routes._snapshot_to_tree(snapshot)

        assert len(tree) == 1
        assert tree[0].name == "src"
        assert tree[0].type == "directory"
        assert tree[0].children is not None
        assert [child.name for child in tree[0].children] == ["app.py"]


def test_snapshot_to_tree_stringifies_unserializable_values() -> None:
    class _Unserializable:
        def __str__(self) -> str:
            return "line1\nline2"

    tree = vault_routes._snapshot_to_tree({"notes.txt": _Unserializable()})

    assert len(tree) == 1
    assert tree[0].name == "notes.txt"
    assert tree[0].lines == 2


def test_snapshot_to_tree_treats_dict_file_content_as_file() -> None:
    tree = vault_routes._snapshot_to_tree({"metadata.json": {"enabled": True}})

    assert len(tree) == 1
    assert tree[0].name == "metadata.json"
    assert tree[0].type == "file"
    assert tree[0].children is None
    assert tree[0].lines == 1


def test_summarize_version_ignores_invalid_file_snapshot() -> None:
    version = SimpleNamespace(
        id=uuid.uuid4(),
        version_number=1,
        created_at=datetime.utcnow(),
        es_score=None,
        commit_message=None,
        diff=None,
        file_snapshot="oops",
        files_changed=[],
    )

    response = vault_routes._summarize_version(version)

    assert response.lines_changed == 0


def test_summarize_version_stringifies_unserializable_snapshot_content() -> None:
    class _Unserializable:
        def __str__(self) -> str:
            return "line1\nline2"

    version = SimpleNamespace(
        id=uuid.uuid4(),
        version_number=1,
        created_at=datetime.utcnow(),
        es_score=None,
        commit_message=None,
        diff=None,
        file_snapshot={"notes.txt": _Unserializable()},
        files_changed=[],
    )

    response = vault_routes._summarize_version(version)

    assert response.lines_changed == 2


def test_summarize_version_normalizes_malformed_fields() -> None:
    version = SimpleNamespace(
        id=uuid.uuid4(),
        version_number=" 7 ",
        created_at="2026-01-02T03:04:05Z",
        es_score="0.55",
        commit_message={"message": "Refine export flow"},
        diff={"diff": "oops"},
        file_snapshot={},
        files_changed=1,
    )

    response = vault_routes._summarize_version(version)

    assert response.version == 7
    assert response.health_score == 0.55
    assert response.prompt_summary == "Version snapshot"
    assert response.lines_changed == 0


@pytest.mark.asyncio
async def test_list_projects_normalizes_malformed_project_fields() -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        name=["demo"],
        language={"name": "python"},
        last_activity="2026-01-02T03:04:05Z",
        created_at=None,
        latest_es_score="0.42",
        total_sessions=" 8 ",
        versions=[],
        file_tree=None,
    )

    response = await vault_routes.list_projects(
        current_user=SimpleNamespace(id="user-1"),
        db=_ListProjectsDB([project]),
    )

    assert len(response) == 1
    assert response[0].name == "Project"
    assert response[0].language == "Unknown"
    assert response[0].health_score == 0.42
    assert response[0].session_count == 8


@pytest.mark.asyncio
async def test_list_projects_ignores_malformed_versions() -> None:
    valid_version = SimpleNamespace(
        id=uuid.uuid4(),
        version_number=2,
        created_at="2026-01-02T03:04:05Z",
        es_score="0.6",
        commit_message="Ship snapshot",
        diff=None,
        file_snapshot={"app/main.py": "print('hi')\n"},
        files_changed=[],
    )
    project = SimpleNamespace(
        id=uuid.uuid4(),
        name="demo",
        language="python",
        last_activity="2026-01-02T03:04:05Z",
        created_at=None,
        latest_es_score="0.42",
        total_sessions="1",
        versions=["oops", valid_version],
        file_tree=None,
    )

    response = await vault_routes.list_projects(
        current_user=SimpleNamespace(id="user-1"),
        db=_ListProjectsDB([project]),
    )

    assert len(response) == 1
    assert response[0].line_count == 1
    assert [version.version for version in response[0].versions] == [2]


@pytest.mark.asyncio
async def test_create_webhook_export_rejects_blank_webhook_url(monkeypatch) -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        user_id="user-1",
        name="demo",
        language="python",
        framework="fastapi",
    )

    async def fail_get_project_versions(self, project_uuid):
        raise AssertionError("get_project_versions should not run for blank webhook URLs")

    monkeypatch.setattr(
        vault_routes.VaultService,
        "get_project_versions",
        fail_get_project_versions,
    )

    with pytest.raises(HTTPException) as exc_info:
        await vault_routes.create_export(
            vault_routes.ExportRequest(
                project_id=str(project.id),
                export_type="webhook",
                webhook_url="   ",
            ),
            current_user=SimpleNamespace(id="user-1"),
            db=_CreateExportDB(project),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Webhook URL is required for webhook exports"


@pytest.mark.asyncio
async def test_create_webhook_export_rejects_invalid_webhook_url(monkeypatch) -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        user_id="user-1",
        name="demo",
        language="python",
        framework="fastapi",
    )

    async def fail_get_project_versions(self, project_uuid):
        raise AssertionError("get_project_versions should not run for invalid webhook URLs")

    monkeypatch.setattr(
        vault_routes.VaultService,
        "get_project_versions",
        fail_get_project_versions,
    )

    with pytest.raises(HTTPException) as exc_info:
        await vault_routes.create_export(
            vault_routes.ExportRequest(
                project_id=str(project.id),
                export_type="webhook",
                webhook_url="ftp://example.com/webhook",
            ),
            current_user=SimpleNamespace(id="user-1"),
            db=_CreateExportDB(project),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Webhook URL must be a valid HTTP(S) URL"


@pytest.mark.asyncio
async def test_create_webhook_export_rejects_projects_without_versions(
    monkeypatch,
) -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        user_id="user-1",
        name="demo",
        language="python",
        framework="fastapi",
    )

    async def fake_get_project_versions(self, project_uuid):
        return []

    monkeypatch.setattr(
        vault_routes.VaultService,
        "get_project_versions",
        fake_get_project_versions,
    )

    with pytest.raises(HTTPException) as exc_info:
        await vault_routes.create_export(
            vault_routes.ExportRequest(
                project_id=str(project.id),
                export_type="webhook",
                webhook_url="https://example.com/webhook",
            ),
            current_user=SimpleNamespace(id="user-1"),
            db=_CreateExportDB(project),
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "No project versions available for export"


@pytest.mark.asyncio
async def test_download_export_rejects_empty_latest_snapshots(monkeypatch) -> None:
    export_record = SimpleNamespace(
        id=uuid.uuid4(),
        user_id="user-1",
        project_id=uuid.uuid4(),
        export_type="zip",
        status="completed",
        destination="demo.zip",
    )
    version = SimpleNamespace(file_snapshot={})

    async def fake_get_project_versions(self, project_uuid):
        return [version]

    monkeypatch.setattr(
        vault_routes.VaultService,
        "get_project_versions",
        fake_get_project_versions,
    )

    with pytest.raises(HTTPException) as exc_info:
        await vault_routes.download_export(
            str(export_record.id),
            current_user=SimpleNamespace(id="user-1"),
            db=_DownloadExportDB(export_record),
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "No project files available for export"


@pytest.mark.asyncio
async def test_download_export_rejects_invalid_latest_snapshots(monkeypatch) -> None:
    export_record = SimpleNamespace(
        id=uuid.uuid4(),
        user_id="user-1",
        project_id=uuid.uuid4(),
        export_type="zip",
        status="completed",
        destination="demo.zip",
    )
    version = SimpleNamespace(file_snapshot="oops")

    async def fake_get_project_versions(self, project_uuid):
        return [version]

    monkeypatch.setattr(
        vault_routes.VaultService,
        "get_project_versions",
        fake_get_project_versions,
    )

    with pytest.raises(HTTPException) as exc_info:
        await vault_routes.download_export(
            str(export_record.id),
            current_user=SimpleNamespace(id="user-1"),
            db=_DownloadExportDB(export_record),
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "No project files available for export"


@pytest.mark.asyncio
async def test_list_exports_preserves_destination_without_metadata(monkeypatch) -> None:
    export = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        export_type="webhook",
        status="failed",
        created_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00"),
        file_url=None,
        destination="https://example.com/webhook",
        metadata_=None,
    )

    async def fake_get_exports(self, user_id):
        return [export]

    monkeypatch.setattr(vault_routes.VaultService, "get_exports", fake_get_exports)

    history = await vault_routes.list_exports(
        request=_make_request(),
        current_user=SimpleNamespace(id="user-1"),
        db=_ListExportsDB(),
    )

    assert len(history) == 1
    assert history[0].destination == "https://example.com/webhook"


@pytest.mark.asyncio
async def test_list_exports_tolerates_string_created_at(monkeypatch) -> None:
    export = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        export_type="zip",
        status="completed",
        created_at=" 2026-01-02T03:04:05Z ",
        file_url=None,
        destination="demo.zip",
        metadata_=None,
    )

    async def fake_get_exports(self, user_id):
        return [export]

    monkeypatch.setattr(vault_routes.VaultService, "get_exports", fake_get_exports)

    history = await vault_routes.list_exports(
        current_user=SimpleNamespace(id="user-1"),
        db=_ListExportsDB(),
    )

    assert len(history) == 1
    assert history[0].created_at == "2026-01-02T03:04:05Z"


@pytest.mark.asyncio
async def test_list_exports_uses_relative_download_url_when_api_url_blank(monkeypatch) -> None:
    export = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        export_type="zip",
        status="completed",
        created_at=datetime.utcnow(),
        file_url=None,
        destination="demo.zip",
        metadata_=None,
    )

    async def fake_get_exports(self, user_id):
        return [export]

    monkeypatch.setattr(vault_routes.VaultService, "get_exports", fake_get_exports)
    monkeypatch.setattr(settings, "api_url", "   ")

    history = await vault_routes.list_exports(
        request=_make_request(),
        current_user=SimpleNamespace(id="user-1"),
        db=_ListExportsDB(),
    )

    assert len(history) == 1
    assert history[0].download_url == f"/vault/exports/{export.id}/download"


@pytest.mark.asyncio
async def test_list_exports_prefers_request_api_base_url_header(monkeypatch) -> None:
    export = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        export_type="zip",
        status="completed",
        created_at=datetime.utcnow(),
        file_url=None,
        destination="demo.zip",
        metadata_=None,
    )

    async def fake_get_exports(self, user_id):
        return [export]

    monkeypatch.setattr(vault_routes.VaultService, "get_exports", fake_get_exports)

    history = await vault_routes.list_exports(
        request=_make_request(
            headers=[(b"x-codey-api-base-url", b" https://api.example.com/proxy/ ")]
        ),
        current_user=SimpleNamespace(id="user-1"),
        db=_ListExportsDB(),
    )

    assert len(history) == 1
    assert history[0].download_url == f"https://api.example.com/proxy/vault/exports/{export.id}/download"


@pytest.mark.asyncio
async def test_list_exports_parses_string_metadata(monkeypatch) -> None:
    export = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        export_type="zip",
        status="completed",
        created_at=datetime.utcnow(),
        file_url=None,
        destination=None,
        metadata_='{"project_name": "demo"}',
    )

    async def fake_get_exports(self, user_id):
        return [export]

    monkeypatch.setattr(vault_routes.VaultService, "get_exports", fake_get_exports)

    history = await vault_routes.list_exports(
        request=_make_request(),
        current_user=SimpleNamespace(id="user-1"),
        db=_ListExportsDB(),
    )

    assert len(history) == 1
    assert history[0].destination == "demo"


@pytest.mark.asyncio
async def test_list_exports_fails_closed_for_invalid_metadata(monkeypatch) -> None:
    export = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        export_type="zip",
        status="completed",
        created_at=datetime.utcnow(),
        file_url=None,
        destination=None,
        metadata_="oops",
    )

    async def fake_get_exports(self, user_id):
        return [export]

    monkeypatch.setattr(vault_routes.VaultService, "get_exports", fake_get_exports)

    history = await vault_routes.list_exports(
        current_user=SimpleNamespace(id="user-1"),
        db=_ListExportsDB(),
    )

    assert len(history) == 1
    assert history[0].destination == ""


@pytest.mark.asyncio
async def test_list_exports_normalizes_malformed_export_fields(monkeypatch) -> None:
    export = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        export_type=["zip"],
        status={"state": "completed"},
        created_at="2026-01-02T03:04:05Z",
        file_url={"url": "https://example.com/download.zip"},
        destination={"path": "demo.zip"},
        metadata_={"project_name": ["demo"]},
    )
    project = SimpleNamespace(id=export.project_id, name={"name": "demo"})

    async def fake_get_exports(self, user_id):
        return [export]

    monkeypatch.setattr(vault_routes.VaultService, "get_exports", fake_get_exports)

    history = await vault_routes.list_exports(
        current_user=SimpleNamespace(id="user-1"),
        db=_ListProjectsDB([project]),
    )

    assert len(history) == 1
    assert history[0].project_name == "Project"
    assert history[0].export_type == "unknown"
    assert history[0].status == "unknown"
    assert history[0].download_url is None
    assert history[0].destination == ""


def test_export_to_response_normalizes_malformed_status() -> None:
    export = SimpleNamespace(
        id=uuid.uuid4(),
        status=["processing"],
    )

    response = vault_routes._export_to_response(export)

    assert response.id == str(export.id)
    assert response.status == "unknown"


@pytest.mark.asyncio
async def test_create_download_export_rejects_projects_without_versions(
    monkeypatch,
) -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        user_id="user-1",
        name="demo",
    )

    async def fake_get_project_versions(self, project_uuid):
        return []

    async def fail_export_project(self, *args, **kwargs):
        raise AssertionError("export_project should not run without versions")

    monkeypatch.setattr(
        vault_routes.VaultService,
        "get_project_versions",
        fake_get_project_versions,
    )
    monkeypatch.setattr(
        vault_routes.VaultService,
        "export_project",
        fail_export_project,
    )

    with pytest.raises(HTTPException) as exc_info:
        await vault_routes.create_export(
            vault_routes.ExportRequest(
                project_id=str(project.id),
                export_type="download",
            ),
            current_user=SimpleNamespace(id="user-1"),
            db=_CreateExportDB(project),
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "No project versions available for export"


@pytest.mark.asyncio
async def test_create_download_export_trims_export_type(monkeypatch) -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        user_id="user-1",
        name="demo",
    )
    version = SimpleNamespace(file_snapshot={"main.py": "print('ok')"})
    captured: dict[str, object] = {}
    export_id = uuid.uuid4()

    async def fake_get_project_versions(self, project_uuid):
        return [version]

    async def fake_export_project(
        self,
        user_id,
        project_uuid,
        export_type,
        destination=None,
    ):
        captured["user_id"] = user_id
        captured["project_uuid"] = project_uuid
        captured["export_type"] = export_type
        captured["destination"] = destination
        return SimpleNamespace(id=export_id, status="completed")

    monkeypatch.setattr(
        vault_routes.VaultService,
        "get_project_versions",
        fake_get_project_versions,
    )
    monkeypatch.setattr(
        vault_routes.VaultService,
        "export_project",
        fake_export_project,
    )

    response = await vault_routes.create_export(
        vault_routes.ExportRequest(
            project_id=str(project.id),
            export_type=" download ",
        ),
        current_user=SimpleNamespace(id="user-1"),
        db=_CreateExportDB(project),
    )

    assert response.id == str(export_id)
    assert response.status == "completed"
    assert captured == {
        "user_id": "user-1",
        "project_uuid": project.id,
        "export_type": "zip",
        "destination": "demo.zip",
    }


@pytest.mark.asyncio
async def test_create_github_export_trims_destination_fields(monkeypatch) -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        user_id="user-1",
        name="demo",
    )
    captured: dict[str, object] = {}
    export_id = uuid.uuid4()

    async def fake_export_project(
        self,
        user_id,
        project_uuid,
        export_type,
        destination=None,
    ):
        captured["user_id"] = user_id
        captured["project_uuid"] = project_uuid
        captured["export_type"] = export_type
        captured["destination"] = destination
        return SimpleNamespace(id=export_id, status="completed")

    monkeypatch.setattr(
        vault_routes.VaultService,
        "export_project",
        fake_export_project,
    )

    response = await vault_routes.create_export(
        vault_routes.ExportRequest(
            project_id=str(project.id),
            export_type="github",
            github_repo=" owner/repo ",
            github_branch=" feature/export ",
        ),
        current_user=SimpleNamespace(id="user-1"),
        db=_CreateExportDB(project),
    )

    assert response.id == str(export_id)
    assert response.status == "completed"
    assert captured == {
        "user_id": "user-1",
        "project_uuid": project.id,
        "export_type": "github",
        "destination": "owner/repo (feature/export)",
    }


@pytest.mark.asyncio
async def test_create_download_export_rejects_empty_latest_snapshots(
    monkeypatch,
) -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        user_id="user-1",
        name="demo",
    )
    version = SimpleNamespace(file_snapshot={})

    async def fake_get_project_versions(self, project_uuid):
        return [version]

    async def fail_export_project(self, *args, **kwargs):
        raise AssertionError("export_project should not run with an empty snapshot")

    monkeypatch.setattr(
        vault_routes.VaultService,
        "get_project_versions",
        fake_get_project_versions,
    )
    monkeypatch.setattr(
        vault_routes.VaultService,
        "export_project",
        fail_export_project,
    )

    with pytest.raises(HTTPException) as exc_info:
        await vault_routes.create_export(
            vault_routes.ExportRequest(
                project_id=str(project.id),
                export_type="download",
            ),
            current_user=SimpleNamespace(id="user-1"),
            db=_CreateExportDB(project),
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "No project files available for export"


@pytest.mark.asyncio
async def test_create_download_export_rejects_invalid_latest_snapshots(
    monkeypatch,
) -> None:
    project = SimpleNamespace(
        id=uuid.uuid4(),
        user_id="user-1",
        name="demo",
    )
    version = SimpleNamespace(file_snapshot="oops")

    async def fake_get_project_versions(self, project_uuid):
        return [version]

    async def fail_export_project(self, *args, **kwargs):
        raise AssertionError("export_project should not run with an invalid snapshot")

    monkeypatch.setattr(
        vault_routes.VaultService,
        "get_project_versions",
        fake_get_project_versions,
    )
    monkeypatch.setattr(
        vault_routes.VaultService,
        "export_project",
        fail_export_project,
    )

    with pytest.raises(HTTPException) as exc_info:
        await vault_routes.create_export(
            vault_routes.ExportRequest(
                project_id=str(project.id),
                export_type="download",
            ),
            current_user=SimpleNamespace(id="user-1"),
            db=_CreateExportDB(project),
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "No project files available for export"
