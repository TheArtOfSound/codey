"""Path normalization helpers for generated build plans."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
MAX_PLAN_FILE_PATH_CHARS = 500


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def normalize_plan_file_path(path: object) -> str | None:
    """Return a safe workspace-relative POSIX path, or ``None`` if unsafe."""
    if not isinstance(path, str):
        return None

    candidate = path.replace("\\", "/")
    if _has_ascii_control(candidate):
        return None
    candidate = candidate.strip()
    if not candidate:
        return None
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {
        "'",
        '"',
        "`",
    }:
        candidate = candidate[1:-1].strip()
        if not candidate:
            return None
    if _WINDOWS_DRIVE_PATH.match(candidate):
        return None
    if ":" in candidate or "?" in candidate or "#" in candidate:
        return None

    path_obj = PurePosixPath(candidate)
    if path_obj.is_absolute():
        return None

    parts = [part for part in path_obj.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None

    normalized = PurePosixPath(*parts).as_posix()
    if len(normalized) > MAX_PLAN_FILE_PATH_CHARS:
        return None
    return normalized
