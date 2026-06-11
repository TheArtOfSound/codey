from __future__ import annotations

from codey.saas.archive_utils import (
    MAX_ARCHIVE_PART_CHARS,
    MAX_ARCHIVE_PATH_CHARS,
    dedupe_archive_path,
    safe_archive_path,
    safe_artifact_name,
)


def test_safe_archive_path_preserves_safe_relative_paths() -> None:
    assert safe_archive_path("src/app.py") == "src/app.py"


def test_safe_archive_path_strips_absolute_roots() -> None:
    assert safe_archive_path("/etc/passwd") == "etc/passwd"


def test_safe_archive_path_relocates_traversal_paths() -> None:
    archive_path = safe_archive_path("../secrets.py")

    assert archive_path.startswith("unsafe/")
    assert archive_path.endswith("_secrets.py")
    assert ".." not in archive_path


def test_safe_archive_path_uses_file_fallback_for_blank_paths() -> None:
    assert safe_archive_path("") == "file"
    assert safe_archive_path("   ") == "file"
    assert safe_archive_path("/") == "file"
    assert safe_archive_path("", prefix="demo project") == "demo project/file"


def test_safe_archive_path_applies_safe_prefix() -> None:
    archive_path = safe_archive_path("app/main.py", prefix="demo project")

    assert archive_path == "demo project/app/main.py"


def test_safe_archive_path_sanitizes_nul_bytes() -> None:
    archive_path = safe_archive_path("src/bad\x00name.py", prefix="demo\x00project")

    assert archive_path == "demo_project/src/bad_name.py"
    assert "\x00" not in archive_path


def test_safe_archive_path_sanitizes_ascii_controls() -> None:
    archive_path = safe_archive_path("src/bad\n\tname\x7f.py", prefix="demo\rproject")

    assert archive_path == "demo_project/src/bad__name_.py"
    assert all(ord(char) >= 32 and ord(char) != 127 for char in archive_path)


def test_safe_archive_path_sanitizes_url_query_and_fragment_delimiters() -> None:
    archive_path = safe_archive_path(
        "src/app.py?access_token=secret#client_secret=secret"
    )

    assert archive_path == "src/app.py_access_token=***_client_secret=***"
    assert "=secret" not in archive_path
    assert "?" not in archive_path
    assert "#" not in archive_path


def test_safe_archive_path_redacts_bearer_secret_values() -> None:
    archive_path = safe_archive_path("logs/export?token=Bearer abc123")

    assert archive_path == "logs/export_token=Bearer ***"
    assert "abc123" not in archive_path


def test_safe_archive_path_redacts_authorization_values() -> None:
    archive_path = safe_archive_path("logs/export?authorization=Bearer abc123")

    assert archive_path == "logs/export_authorization=Bearer ***"
    assert "abc123" not in archive_path


def test_safe_archive_path_sanitizes_nul_bytes_in_relocated_leaf() -> None:
    archive_path = safe_archive_path("../bad\x00name.py")

    assert archive_path.startswith("unsafe/")
    assert archive_path.endswith("_bad_name.py")
    assert "\x00" not in archive_path


def test_safe_archive_path_bounds_long_path_parts_with_digest() -> None:
    long_part = "x" * (MAX_ARCHIVE_PART_CHARS + 50)
    archive_path = safe_archive_path(f"src/{long_part}/app.py")
    truncated_part = archive_path.split("/")[1]

    assert len(truncated_part) == MAX_ARCHIVE_PART_CHARS
    assert truncated_part.startswith("x" * 20)
    assert truncated_part != long_part


def test_safe_archive_path_bounds_long_archive_paths_with_digest() -> None:
    long_path = "/".join(f"dir{i:03d}" for i in range(100))
    archive_path = safe_archive_path(f"{long_path}/app.py")

    assert len(archive_path) <= MAX_ARCHIVE_PATH_CHARS
    assert archive_path.startswith("truncated/")
    assert archive_path.endswith("_app.py")


def test_dedupe_archive_path_adds_stable_numeric_suffixes() -> None:
    seen: set[str] = set()

    assert dedupe_archive_path("src/app.py", seen) == "src/app.py"
    assert dedupe_archive_path("src/app.py", seen) == "src/app-2.py"
    assert dedupe_archive_path("src/app.py", seen) == "src/app-3.py"
    assert dedupe_archive_path("README", seen) == "README"
    assert dedupe_archive_path("README", seen) == "README-2"


def test_dedupe_archive_path_sanitizes_unsanitized_input() -> None:
    seen: set[str] = set()

    archive_path = dedupe_archive_path("../app.py?access_token=secret", seen)

    assert archive_path.startswith("unsafe/")
    assert archive_path.endswith("_app.py_access_token=***")
    assert "=secret" not in archive_path
    assert archive_path in seen


def test_dedupe_archive_path_keeps_long_duplicates_bounded() -> None:
    archive_path = "x" * MAX_ARCHIVE_PATH_CHARS
    seen = {archive_path}

    duplicate = dedupe_archive_path(archive_path, seen)

    assert duplicate != archive_path
    assert len(duplicate) <= MAX_ARCHIVE_PATH_CHARS
    assert duplicate in seen


def test_safe_artifact_name_uses_safe_basename() -> None:
    assert (
        safe_artifact_name("../demo build", default="project", suffix=".zip")
        == "demo_build.zip"
    )


def test_safe_artifact_name_sanitizes_default_when_name_is_blank() -> None:
    assert (
        safe_artifact_name("   ", default='../bad "fallback".zip', suffix=".zip")
        == "bad_fallback.zip"
    )


def test_safe_artifact_name_redacts_secret_values() -> None:
    assert (
        safe_artifact_name(
            "demo?access_token=secret.zip",
            default="project",
            suffix=".zip",
        )
        == "demo_access_token.zip"
    )


def test_safe_artifact_name_redacts_secret_values_in_default() -> None:
    assert (
        safe_artifact_name(
            "",
            default="demo?client_secret=secret.zip",
            suffix=".zip",
        )
        == "demo_client_secret.zip"
    )


def test_safe_artifact_name_redacts_authorization_values() -> None:
    assert (
        safe_artifact_name(
            "demo?authorization=Bearer abc123.zip",
            default="project",
            suffix=".zip",
        )
        == "demo_authorization.zip"
    )


def test_safe_artifact_name_uses_stable_fallback_when_default_is_blank() -> None:
    assert safe_artifact_name("", default="...", suffix=".zip") == "artifact.zip"


def test_safe_artifact_name_bounds_long_names_with_digest() -> None:
    artifact_name = safe_artifact_name(
        "x" * 300,
        default="project",
        suffix=".zip",
    )

    stem = artifact_name.removesuffix(".zip")
    assert len(stem) == 120
    assert stem.startswith("x" * 111)
    assert artifact_name.endswith(".zip")
