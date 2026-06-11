from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import uuid
import zipfile

from fastapi import BackgroundTasks
from fastapi.responses import FileResponse
import pytest

import codey.saas.api.build_routes as build_routes


class _ProjectStub:
    def __init__(self, *, download_url: str | None, status: str = "completed") -> None:
        self.id = uuid.uuid4()
        self.name = "demo"
        self.description = "demo project"
        self.status = status
        self.current_phase = 1
        self.total_phases = 1
        self.files_planned = 1
        self.files_completed = 1
        self.lines_generated = 10
        self.credits_charged = 1
        self.nfet_es_score_final = None
        self.nfet_phase_final = None
        self.project_plan = None
        self.file_tree = None
        self.stack = None
        self.download_url = download_url
        self.github_repo_url = None
        self.started_at = datetime.utcnow()
        self.completed_at = datetime.utcnow()


class _EmptyBuildFilesResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _EmptyBuildFilesDB:
    async def execute(self, *args, **kwargs):
        return _EmptyBuildFilesResult()

    async def flush(self) -> None:
        return None


class _BuildFilesResult:
    def __init__(self, files) -> None:
        self._files = files

    def scalars(self):
        return self

    def all(self):
        return self._files


class _BuildFilesDB:
    def __init__(self, files) -> None:
        self._files = files

    async def execute(self, *args, **kwargs):
        return _BuildFilesResult(self._files)

    async def flush(self) -> None:
        return None


class _FlushFailingBuildFilesDB(_BuildFilesDB):
    async def flush(self) -> None:
        raise RuntimeError("flush failed")


def test_coerce_existing_zip_path_rejects_non_zip_paths(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("not a zip", encoding="utf-8")

    assert build_routes._coerce_existing_zip_path(str(artifact)) is None


def test_coerce_existing_zip_path_rejects_relative_zip_paths() -> None:
    assert build_routes._coerce_existing_zip_path("artifact.zip") is None
    assert build_routes._coerce_existing_zip_path("../artifact.zip") is None


def test_coerce_existing_zip_path_rejects_control_character_paths() -> None:
    assert build_routes._coerce_existing_zip_path("/tmp/bad\nname.zip") is None
    assert build_routes._coerce_existing_zip_path("/tmp/bad\tname.zip") is None
    assert build_routes._coerce_existing_zip_path("/tmp/bad\x7fname.zip") is None


def test_coerce_existing_zip_path_accepts_codey_temp_zip_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    temp_root = tmp_path / "tmp"
    artifact = temp_root / "codey_builds" / "artifact.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"zip")

    monkeypatch.setattr(build_routes.tempfile, "gettempdir", lambda: str(temp_root))

    assert build_routes._coerce_existing_zip_path(str(artifact)) == artifact.resolve(
        strict=False
    )


def test_coerce_existing_zip_path_rejects_zip_paths_outside_temp_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    temp_root = tmp_path / "tmp"
    allowed = temp_root / "codey_builds" / "artifact.zip"
    outside = tmp_path / "outside" / "artifact.zip"

    monkeypatch.setattr(build_routes.tempfile, "gettempdir", lambda: str(temp_root))

    assert build_routes._coerce_existing_zip_path(str(allowed)) == allowed.resolve(
        strict=False
    )
    assert build_routes._coerce_existing_zip_path(str(outside)) is None


def test_coerce_existing_zip_path_rejects_zip_paths_outside_codey_temp_dirs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    temp_root = tmp_path / "tmp"
    allowed_request = temp_root / "codey_build_abc123" / "artifact.zip"
    allowed_shared = temp_root / "codey_builds" / "artifact.zip"
    root_zip = temp_root / "artifact.zip"
    sibling_zip = temp_root / "other" / "artifact.zip"
    for path in (allowed_request, allowed_shared, root_zip, sibling_zip):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"zip")

    monkeypatch.setattr(build_routes.tempfile, "gettempdir", lambda: str(temp_root))

    assert build_routes._coerce_existing_zip_path(
        str(allowed_request)
    ) == allowed_request.resolve(strict=False)
    assert build_routes._coerce_existing_zip_path(
        str(allowed_shared)
    ) == allowed_shared.resolve(strict=False)
    assert build_routes._coerce_existing_zip_path(str(root_zip)) is None
    assert build_routes._coerce_existing_zip_path(str(sibling_zip)) is None


def test_coerce_generated_file_content_accepts_structured_text_blocks() -> None:
    assert (
        build_routes._coerce_generated_file_content(
            {
                "content": [
                    {"type": "text", "text": "console.log('one')"},
                    {"type": "image", "source": "ignored"},
                    {"type": "text", "text": "console.log('two')"},
                ]
            }
        )
        == "console.log('one')\nconsole.log('two')"
    )
    assert (
        build_routes._coerce_generated_file_content(
            {"content": {"type": "image", "source": "ignored"}, "code": "print('ok')"}
        )
        == "print('ok')"
    )


def test_count_generated_file_lines_ignores_blank_and_trailing_lines() -> None:
    assert build_routes._count_generated_file_lines("") == 0
    assert build_routes._count_generated_file_lines("print('ok')\n") == 1
    assert (
        build_routes._count_generated_file_lines("\nprint('one')\n\nprint('two')\n")
        == 2
    )


@pytest.mark.asyncio
async def test_get_download_returns_public_route_for_existing_artifact(monkeypatch) -> None:
    with tempfile.TemporaryDirectory(prefix="codey_build_") as tmpdir:
        zip_path = Path(tmpdir) / "demo.zip"
        zip_path.write_bytes(b"zip")
        project = _ProjectStub(download_url=str(zip_path))

        async def fake_get_project(project_id, current_user, db):
            return project

        async def fail_generate(*args, **kwargs):
            raise AssertionError("zip generation should not run")

        monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
        monkeypatch.setattr(build_routes, "_generate_project_zip", fail_generate)

        response = await build_routes.get_download(
            "proj-1",
            current_user=object(),
            db=object(),
        )

        assert response.download_url == "/build/proj-1/download/zip"
        assert response.filename == "demo.zip"
        assert response.size_bytes == 3


@pytest.mark.asyncio
async def test_get_download_regenerates_when_download_url_points_to_directory(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_path = Path(tmpdir) / "artifact-dir"
        bad_path.mkdir()
        zip_path = Path(tmpdir) / "regenerated.zip"
        zip_path.write_bytes(b"zip")
        project = _ProjectStub(download_url=str(bad_path))

        async def fake_get_project(project_id, current_user, db):
            return project

        async def fake_generate(project_arg, db):
            project_arg.download_url = str(zip_path)
            return zip_path, zip_path.stat().st_size

        monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
        monkeypatch.setattr(build_routes, "_generate_project_zip", fake_generate)

        response = await build_routes.get_download(
            "proj-1",
            current_user=object(),
            db=object(),
        )

        assert response.download_url == "/build/proj-1/download/zip"
        assert response.filename == "regenerated.zip"
        assert response.size_bytes == 3


@pytest.mark.asyncio
async def test_get_download_regenerates_when_download_url_is_malformed(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "regenerated.zip"
        zip_path.write_bytes(b"zip")
        project = _ProjectStub(download_url=["bad-path"])

        async def fake_get_project(project_id, current_user, db):
            return project

        async def fake_generate(project_arg, db):
            project_arg.download_url = str(zip_path)
            return zip_path, zip_path.stat().st_size

        monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
        monkeypatch.setattr(build_routes, "_generate_project_zip", fake_generate)

        response = await build_routes.get_download(
            "proj-1",
            current_user=object(),
            db=object(),
        )

        assert response.download_url == "/build/proj-1/download/zip"
        assert response.filename == "regenerated.zip"
        assert response.size_bytes == 3


@pytest.mark.asyncio
async def test_get_download_regenerates_when_cached_zip_stat_fails(monkeypatch) -> None:
    class _RaceyPath:
        name = "stale.zip"

        def is_file(self) -> bool:
            return True

        def stat(self):
            raise OSError("artifact disappeared")

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "regenerated.zip"
        zip_path.write_bytes(b"zip")
        project = _ProjectStub(download_url="/tmp/stale.zip")

        async def fake_get_project(project_id, current_user, db):
            return project

        async def fake_generate(project_arg, db):
            project_arg.download_url = str(zip_path)
            return zip_path, zip_path.stat().st_size

        monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
        monkeypatch.setattr(build_routes, "_coerce_existing_zip_path", lambda _value: _RaceyPath())
        monkeypatch.setattr(build_routes, "_generate_project_zip", fake_generate)

        response = await build_routes.get_download(
            "proj-1",
            current_user=object(),
            db=object(),
        )

        assert response.download_url == "/build/proj-1/download/zip"
        assert response.filename == "regenerated.zip"
        assert response.size_bytes == 3


@pytest.mark.asyncio
async def test_generate_project_zip_rejects_empty_completed_file_sets() -> None:
    project = _ProjectStub(download_url=None)

    with pytest.raises(build_routes.HTTPException) as exc_info:
        await build_routes._generate_project_zip(project, _EmptyBuildFilesDB())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "No completed build files available for download"


@pytest.mark.asyncio
async def test_generate_project_zip_rejects_completed_files_without_content() -> None:
    project = _ProjectStub(download_url=None)
    files = [
        type("BuildFileStub", (), {"file_path": "README.md", "content": None})(),
    ]

    with pytest.raises(build_routes.HTTPException) as exc_info:
        await build_routes._generate_project_zip(project, _BuildFilesDB(files))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "No completed build files available for download"


@pytest.mark.asyncio
async def test_generate_project_zip_includes_empty_string_files() -> None:
    project = _ProjectStub(download_url=None)
    files = [
        type("BuildFileStub", (), {"file_path": "app/__init__.py", "content": ""})(),
    ]

    zip_path, _size = await build_routes._generate_project_zip(
        project,
        _BuildFilesDB(files),
    )

    try:
        with zipfile.ZipFile(zip_path) as zf:
            assert zf.namelist() == ["app/__init__.py"]
            assert zf.read("app/__init__.py") == b""
    finally:
        build_routes._cleanup_generated_zip(zip_path)


@pytest.mark.asyncio
async def test_generate_project_zip_deduplicates_colliding_archive_paths() -> None:
    project = _ProjectStub(download_url=None)
    files = [
        type("BuildFileStub", (), {"file_path": "src/a:b.py", "content": "one"})(),
        type("BuildFileStub", (), {"file_path": "src/a_b.py", "content": "two"})(),
    ]

    zip_path, _size = await build_routes._generate_project_zip(
        project,
        _BuildFilesDB(files),
    )

    try:
        with zipfile.ZipFile(zip_path) as zf:
            assert zf.namelist() == ["src/a_b.py", "src/a_b-2.py"]
            assert zf.read("src/a_b.py").decode("utf-8") == "one"
            assert zf.read("src/a_b-2.py").decode("utf-8") == "two"
    finally:
        build_routes._cleanup_generated_zip(zip_path)


@pytest.mark.asyncio
async def test_generate_project_zip_cleans_temp_dir_when_flush_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project = _ProjectStub(download_url="/tmp/previous.zip")
    files = [type("BuildFileStub", (), {"file_path": "main.py", "content": "print(1)"})()]
    temp_dir = tmp_path / "codey_build_failed_flush"

    def fake_mkdtemp(*args, **kwargs):
        temp_dir.mkdir()
        return str(temp_dir)

    monkeypatch.setattr(build_routes.tempfile, "mkdtemp", fake_mkdtemp)

    with pytest.raises(RuntimeError, match="flush failed"):
        await build_routes._generate_project_zip(
            project,
            _FlushFailingBuildFilesDB(files),
        )

    assert temp_dir.exists() is False
    assert project.download_url == "/tmp/previous.zip"


@pytest.mark.asyncio
async def test_download_project_zip_serves_artifact_and_schedules_cleanup(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "demo.zip"
        zip_path.write_bytes(b"zip")
        project = _ProjectStub(download_url=None)

        async def fake_get_project(project_id, current_user, db):
            return project

        async def fake_generate(project_arg, db):
            project_arg.download_url = str(zip_path)
            return zip_path, zip_path.stat().st_size

        monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
        monkeypatch.setattr(build_routes, "_generate_project_zip", fake_generate)

        background_tasks = BackgroundTasks()
        response = await build_routes.download_project_zip(
            "proj-1",
            background_tasks=background_tasks,
            current_user=object(),
            db=object(),
        )

        assert isinstance(response, FileResponse)
        assert Path(response.path) == zip_path
        assert len(background_tasks.tasks) == 1

        task = background_tasks.tasks[0]
        task.func(*task.args, **task.kwargs)

        assert zip_path.exists() is False


@pytest.mark.asyncio
async def test_download_project_zip_preserves_existing_artifact_without_cleanup(monkeypatch) -> None:
    with tempfile.TemporaryDirectory(prefix="codey_build_") as tmpdir:
        zip_path = Path(tmpdir) / "demo.zip"
        zip_path.write_bytes(b"zip")
        project = _ProjectStub(download_url=str(zip_path))

        async def fake_get_project(project_id, current_user, db):
            return project

        async def fail_generate(*args, **kwargs):
            raise AssertionError("zip generation should not run")

        monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
        monkeypatch.setattr(build_routes, "_generate_project_zip", fail_generate)

        background_tasks = BackgroundTasks()
        response = await build_routes.download_project_zip(
            "proj-1",
            background_tasks=background_tasks,
            current_user=object(),
            db=object(),
        )

        assert isinstance(response, FileResponse)
        assert Path(response.path) == zip_path.resolve(strict=False)
        assert background_tasks.tasks == []


@pytest.mark.asyncio
async def test_download_project_zip_regenerates_when_download_url_is_malformed(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "demo.zip"
        zip_path.write_bytes(b"zip")
        project = _ProjectStub(download_url=["bad-path"])

        async def fake_get_project(project_id, current_user, db):
            return project

        async def fake_generate(project_arg, db):
            project_arg.download_url = str(zip_path)
            return zip_path, zip_path.stat().st_size

        monkeypatch.setattr(build_routes, "_get_project", fake_get_project)
        monkeypatch.setattr(build_routes, "_generate_project_zip", fake_generate)

        background_tasks = BackgroundTasks()
        response = await build_routes.download_project_zip(
            "proj-1",
            background_tasks=background_tasks,
            current_user=object(),
            db=object(),
        )

        assert isinstance(response, FileResponse)
        assert Path(response.path) == zip_path
        assert len(background_tasks.tasks) == 1
        assert zip_path.exists() is True


def test_cleanup_generated_zip_preserves_shared_build_directory(tmp_path: Path) -> None:
    shared_dir = tmp_path / "codey_builds"
    shared_dir.mkdir()
    zip_path = shared_dir / "demo.zip"
    zip_path.write_bytes(b"zip")

    build_routes._cleanup_generated_zip(zip_path)

    assert zip_path.exists() is False
    assert shared_dir.exists() is True


def test_cleanup_generated_zip_preserves_generated_named_dir_outside_temp_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    temp_root = tmp_path / "tmp"
    outside_dir = tmp_path / "outside" / "codey_build_not_temp"
    outside_dir.mkdir(parents=True)
    zip_path = outside_dir / "demo.zip"
    zip_path.write_bytes(b"zip")

    monkeypatch.setattr(build_routes.tempfile, "gettempdir", lambda: str(temp_root))

    build_routes._cleanup_generated_zip(zip_path)

    assert zip_path.exists() is False
    assert outside_dir.exists() is True


def test_project_to_response_exposes_download_route_for_completed_projects_without_cached_zip() -> None:
    project = _ProjectStub(download_url=None)

    response = build_routes._project_to_response(project)

    assert response.download_url == f"/build/{project.id}/download/zip"


def test_project_to_response_hides_download_route_for_non_completed_projects() -> None:
    project = _ProjectStub(download_url="/tmp/demo.zip", status="paused")

    response = build_routes._project_to_response(project)

    assert response.download_url is None


def test_project_to_response_tolerates_string_timestamps() -> None:
    project = _ProjectStub(download_url=None)
    project.started_at = " 2026-01-02T03:04:05Z "
    project.completed_at = "2026-01-02T03:05:05Z"

    response = build_routes._project_to_response(project)

    assert response.started_at == "2026-01-02T03:04:05Z"
    assert response.completed_at == "2026-01-02T03:05:05Z"


def test_project_to_response_coerces_malformed_fields() -> None:
    project = _ProjectStub(download_url=None)
    project.name = ["demo"]
    project.description = {"text": "demo project"}
    project.status = ["completed"]
    project.current_phase = "2"
    project.total_phases = {"value": 4}
    project.files_planned = "7"
    project.files_completed = {"value": 1}
    project.lines_generated = "10"
    project.credits_charged = ["1"]
    project.nfet_es_score_final = "0.25"
    project.nfet_phase_final = ["validate"]
    project.github_repo_url = {"url": "https://github.com/owner/repo"}
    project.project_plan = ["broken-plan"]
    project.file_tree = "broken-tree"
    project.stack = ["python"]

    response = build_routes._project_to_response(project)

    assert response.name is None
    assert response.description is None
    assert response.status == "unknown"
    assert response.current_phase == 2
    assert response.total_phases == 0
    assert response.files_planned == 7
    assert response.files_completed == 0
    assert response.lines_generated == 10
    assert response.credits_charged == 0
    assert response.nfet_es_score_final == 0.25
    assert response.nfet_phase_final is None
    assert response.github_repo_url is None
    assert response.project_plan is None
    assert response.file_tree is None
    assert response.stack is None
    assert response.download_url is None


def test_project_to_response_exposes_safe_github_repo_url() -> None:
    project = _ProjectStub(download_url=None)
    project.github_repo_url = " https://github.com/owner/repo "

    response = build_routes._project_to_response(project)

    assert response.github_repo_url == "https://github.com/owner/repo"


@pytest.mark.parametrize(
    "github_repo_url",
    [
        "https://github.com/owner/repo?access_token=secret",
        "https://github.com/owner/repo#readme",
        "https://user:secret@github.com/owner/repo",
        "https://github.com:not-a-port/owner/repo",
        "https:///owner/repo",
        "https://github.com/owner/repo\r\nbad",
        "github.com/owner/repo",
        "git@github.com:owner/repo",
        "javascript:alert(1)",
        "ftp://github.com/owner/repo",
    ],
)
def test_project_to_response_rejects_unsafe_github_repo_url_shapes(
    github_repo_url: str,
) -> None:
    project = _ProjectStub(download_url=None)
    project.github_repo_url = github_repo_url

    response = build_routes._project_to_response(project)

    assert response.github_repo_url is None


def test_template_to_response_coerces_malformed_fields() -> None:
    response = build_routes._template_to_response(
        {
            "id": ["saas-starter"],
            "name": {"name": "SaaS Starter"},
            "description": ["starter"],
            "icon": {"icon": "rocket"},
            "estimated_credits": " 25 ",
            "languages": [" TypeScript ", None, 7, " ", "Python"],
            "files_count": {"count": 32},
        }
    )

    assert response.id == ""
    assert response.name == "Template"
    assert response.description == ""
    assert response.icon == ""
    assert response.estimated_credits == 25
    assert response.languages == ["TypeScript", "Python"]
    assert response.files_count == 0


def test_build_file_helpers_tolerate_string_generated_at() -> None:
    build_file = type(
        "BuildFileStub",
        (),
        {
            "id": uuid.uuid4(),
            "file_path": "src/app.py",
            "content": "print('hi')",
            "line_count": 1,
            "phase": 1,
            "status": "completed",
            "stress_score": 0.1,
            "validation_passed": True,
            "generated_at": " 2026-01-02T03:05:05Z ",
        },
    )()

    summary = build_routes._file_to_response(build_file)
    detail = build_routes._file_to_detail(build_file)

    assert summary.generated_at == "2026-01-02T03:05:05Z"
    assert detail.generated_at == "2026-01-02T03:05:05Z"


def test_build_file_detail_preserves_empty_file_content() -> None:
    build_file = type(
        "BuildFileStub",
        (),
        {
            "id": uuid.uuid4(),
            "file_path": "app/__init__.py",
            "content": "",
            "line_count": 0,
            "phase": 1,
            "status": "completed",
            "stress_score": None,
            "validation_passed": True,
            "generated_at": None,
        },
    )()

    detail = build_routes._file_to_detail(build_file)

    assert detail.content == ""


def test_build_file_helpers_coerce_malformed_fields() -> None:
    build_file = type(
        "BuildFileStub",
        (),
        {
            "id": uuid.uuid4(),
            "file_path": ["src/app.py"],
            "content": {"text": "print('hi')"},
            "line_count": "1",
            "phase": {"value": 1},
            "status": ["completed"],
            "stress_score": "0.1",
            "validation_passed": "yes",
            "generated_at": None,
        },
    )()

    summary = build_routes._file_to_response(build_file)
    detail = build_routes._file_to_detail(build_file)

    assert summary.file_path == ""
    assert summary.line_count == 1
    assert summary.phase == 0
    assert summary.status == "unknown"
    assert summary.stress_score == 0.1
    assert summary.validation_passed is None
    assert detail.content is None
