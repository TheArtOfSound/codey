from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from fastapi import HTTPException, status
from pydantic import ValidationError

import codey.saas.api.health_analysis as health_analysis
from codey.saas.api.health_analysis import (
    _coerce_health_metric,
    _coerce_health_text,
    _coerce_top_stress_components,
    _dedupe_upload_destination,
    _redact_health_error,
    _round_health_metric,
    _safe_upload_destination,
)


def test_analyze_code_request_rejects_blank_code() -> None:
    with pytest.raises(ValidationError):
        health_analysis.AnalyzeCodeRequest(code="   ")


def test_analyze_code_request_preserves_non_blank_code() -> None:
    code = "\nprint('ok')\n"

    request = health_analysis.AnalyzeCodeRequest(code=code)

    assert request.code == code


def test_round_health_metric_clamps_non_finite_and_malformed_values() -> None:
    assert _coerce_health_metric(0.60001) == 0.60001
    assert _coerce_health_metric(float("inf")) == 1_000_000.0
    assert _round_health_metric(float("inf")) == 1_000_000.0
    assert _round_health_metric(float("-inf")) == 0.0
    assert _round_health_metric(float("nan")) == 0.0
    assert _round_health_metric("bad") == 0.0
    assert _round_health_metric(-3.0) == 0.0
    assert _round_health_metric(0.123456) == 0.1235


def test_coerce_top_stress_components_skips_malformed_entries() -> None:
    components = _coerce_top_stress_components(
        [
            ("core.py", "0.75"),
            {"id": "api.py", "stress": float("inf")},
            ("", 0.9),
            ["bad-node"],
            "malformed",
            (["unhashable"], 0.8),
            ("ignored.py", 0.9),
        ],
        limit=2,
    )

    assert components == [("core.py", 0.75), ("api.py", 1_000_000.0)]
    assert _coerce_top_stress_components(None, limit=5) == []


def test_coerce_health_text_handles_malformed_values() -> None:
    assert _coerce_health_text(" core.py ", "unknown") == "core.py"
    assert _coerce_health_text("", "unknown") == "unknown"
    assert _coerce_health_text(None, "unknown") == "unknown"
    assert _coerce_health_text(123, "unknown") == "123"


def test_redact_health_error_hides_common_secret_shapes() -> None:
    message = _redact_health_error(
        "analysis failed for https://user:secret@example.test/repo?access_token=access123&client_secret=client123 "
        "api_key=key123 auth_token=auth123 refresh_token=refresh123 "
        "password=pw123 operator@example.test authorization=Bearer bearer123"
    )

    assert "user:secret" not in message
    assert "secret@example.test" not in message
    assert "access123" not in message
    assert "client123" not in message
    assert "key123" not in message
    assert "auth123" not in message
    assert "refresh123" not in message
    assert "pw123" not in message
    assert "bearer123" not in message
    assert "operator@example.test" not in message
    assert "https://***@example.test/repo?access_token=***&client_secret=***" in message
    assert "api_key=***" in message
    assert "auth_token=***" in message
    assert "refresh_token=***" in message
    assert "password=***" in message
    assert "***@example.test" in message
    assert "authorization=Bearer ***" in message


def test_safe_upload_destination_preserves_nested_relative_paths(tmp_path: Path) -> None:
    dest = _safe_upload_destination(tmp_path, "src/components/app.py")

    assert dest == (tmp_path / "src" / "components" / "app.py").resolve()


def test_dedupe_upload_destination_preserves_duplicate_uploads(tmp_path: Path) -> None:
    seen: set[Path] = set()
    first = tmp_path / "src" / "app.py"

    assert _dedupe_upload_destination(first, seen) == first
    assert _dedupe_upload_destination(first, seen) == tmp_path / "src" / "app-2.py"
    assert _dedupe_upload_destination(first, seen) == tmp_path / "src" / "app-3.py"


def test_dedupe_upload_destination_bounds_duplicate_filename_parts(tmp_path: Path) -> None:
    seen: set[Path] = set()
    suffix = ".py"
    filename = (
        f"{'a' * (health_analysis._MAX_ANALYZE_UPLOAD_PATH_PART_CHARS - len(suffix))}"
        f"{suffix}"
    )
    first = tmp_path / filename

    duplicate = _dedupe_upload_destination(first, seen)
    deduped_duplicate = _dedupe_upload_destination(first, seen)

    assert duplicate == first
    assert deduped_duplicate.name.endswith("-2.py")
    assert len(deduped_duplicate.name) <= health_analysis._MAX_ANALYZE_UPLOAD_PATH_PART_CHARS


@pytest.mark.asyncio
async def test_analyze_code_normalizes_malformed_recommendations(monkeypatch) -> None:
    def fake_analyze_code(code: str, filename: str, language: str):
        return {
            "phase": "Excellent",
            "health_score": 0.9,
            "coherence": 0.2,
            "stability": 0.8,
            "total_nodes": 3,
            "total_edges": 2,
            "mean_coupling": 0.1,
            "mean_cohesion": 0.9,
            "highest_stress_component": "core.py",
            "highest_stress_value": 0.2,
            "top_components": [],
            "summary": "Healthy",
            "recommendations": [" Keep going ", 7, "", None],
        }

    monkeypatch.setattr(
        health_analysis,
        "_analyze_code",
        fake_analyze_code,
    )

    response = await health_analysis.analyze_code(
        health_analysis.AnalyzeCodeRequest(code="print('ok')"),
        current_user=object(),
    )

    assert response.report.phase == "Excellent"
    assert response.recommendations == ["Keep going"]


@pytest.mark.asyncio
async def test_analyze_code_redacts_credentials_from_failure_detail(monkeypatch) -> None:
    def fake_analyze_code(code: str, filename: str, language: str):
        raise RuntimeError("analysis failed https://user:secret@example.test/repo")

    monkeypatch.setattr(
        health_analysis,
        "_analyze_code",
        fake_analyze_code,
    )

    with pytest.raises(HTTPException) as exc_info:
        await health_analysis.analyze_code(
            health_analysis.AnalyzeCodeRequest(code="print('ok')"),
            current_user=object(),
        )

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "secret" not in exc_info.value.detail
    assert "https://***@example.test/repo" in exc_info.value.detail


@pytest.mark.parametrize("filename", ["../secrets.py", "..\\secrets.py", "nested/../../secrets.py"])
def test_safe_upload_destination_rejects_traversal(filename: str, tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _safe_upload_destination(tmp_path, filename)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid upload filename"


@pytest.mark.parametrize(
    "filename",
    [
        "bad\x00.py",
        "src/\x00payload.py",
        "bad\nname.py",
        "bad\tname.py",
        "bad\x7fname.py",
    ],
)
def test_safe_upload_destination_rejects_control_characters(
    filename: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _safe_upload_destination(tmp_path, filename)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid upload filename"


@pytest.mark.parametrize("filename", ["/etc/passwd", "C:\\temp\\payload.py"])
def test_safe_upload_destination_rejects_absolute_like_paths(
    filename: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _safe_upload_destination(tmp_path, filename)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid upload filename"


def test_safe_upload_destination_rejects_overlong_path_parts(tmp_path: Path) -> None:
    filename = "a" * (health_analysis._MAX_ANALYZE_UPLOAD_PATH_PART_CHARS + 1)

    with pytest.raises(HTTPException) as exc_info:
        _safe_upload_destination(tmp_path, filename)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid upload filename"


def test_safe_upload_destination_rejects_overlong_paths(tmp_path: Path) -> None:
    filename = "/".join(["nested"] * 100)

    with pytest.raises(HTTPException) as exc_info:
        _safe_upload_destination(tmp_path, filename)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid upload filename"


@pytest.mark.asyncio
async def test_analyze_upload_rejects_empty_file_list() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await health_analysis.analyze_upload(
            files=[],
            current_user=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "No files uploaded"


@pytest.mark.asyncio
async def test_analyze_upload_rejects_too_many_files(monkeypatch) -> None:
    monkeypatch.setattr(health_analysis, "_MAX_ANALYZE_UPLOAD_FILES", 1)

    with pytest.raises(HTTPException) as exc_info:
        await health_analysis.analyze_upload(
            files=[
                UploadFile(filename="one.py", file=BytesIO(b"print('one')")),
                UploadFile(filename="two.py", file=BytesIO(b"print('two')")),
            ],
            current_user=object(),
        )

    assert exc_info.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert exc_info.value.detail == "Too many uploaded files"


@pytest.mark.asyncio
async def test_analyze_upload_preserves_http_exceptions(monkeypatch) -> None:
    upload = UploadFile(filename="bad.py", file=BytesIO(b"print('hello')"))

    def fail_safe_upload_destination(temp_dir: Path, filename: str | None) -> Path:
        raise HTTPException(status_code=400, detail="Invalid upload filename")

    monkeypatch.setattr(
        health_analysis,
        "_safe_upload_destination",
        fail_safe_upload_destination,
    )

    with pytest.raises(HTTPException) as exc_info:
        await health_analysis.analyze_upload(
            files=[upload],
            current_user=object(),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid upload filename"


@pytest.mark.asyncio
async def test_analyze_upload_rejects_oversized_files(monkeypatch) -> None:
    upload = UploadFile(filename="large.py", file=BytesIO(b"print('too large')"))

    monkeypatch.setattr(health_analysis, "_MAX_ANALYZE_UPLOAD_BYTES", 4)

    with pytest.raises(HTTPException) as exc_info:
        await health_analysis.analyze_upload(
            files=[upload],
            current_user=object(),
        )

    assert exc_info.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert exc_info.value.detail == "Uploaded file is too large"


@pytest.mark.asyncio
async def test_analyze_upload_rejects_oversized_total(monkeypatch) -> None:
    monkeypatch.setattr(health_analysis, "_MAX_ANALYZE_UPLOAD_BYTES", 10)
    monkeypatch.setattr(health_analysis, "_MAX_ANALYZE_UPLOAD_TOTAL_BYTES", 10)

    with pytest.raises(HTTPException) as exc_info:
        await health_analysis.analyze_upload(
            files=[
                UploadFile(filename="one.py", file=BytesIO(b"123456")),
                UploadFile(filename="two.py", file=BytesIO(b"abcdef")),
            ],
            current_user=object(),
        )

    assert exc_info.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert exc_info.value.detail == "Uploaded files are too large"
