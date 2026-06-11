from __future__ import annotations

import hashlib
import re
from pathlib import Path
from pathlib import PurePosixPath

MAX_ARTIFACT_STEM_CHARS = 120
MAX_ARCHIVE_PART_CHARS = 180
MAX_ARCHIVE_PATH_CHARS = 512
_ARCHIVE_DIGEST_CHARS = 8
_ARCHIVE_SECRET_VALUE_RE = re.compile(
    r"(?i)\b("
    r"api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|passwd|secret|token|authorization"
    r")(\s*[:=]\s*)(?:bearer\s+)?[^/?#&\s]+"
)


def _stable_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[
        :_ARCHIVE_DIGEST_CHARS
    ]


def _bound_archive_part(part: str) -> str:
    if len(part) <= MAX_ARCHIVE_PART_CHARS:
        return part

    digest = _stable_digest(part)
    keep_chars = MAX_ARCHIVE_PART_CHARS - len(digest) - 1
    return f"{part[:keep_chars]}_{digest}"


def _redact_archive_part_secrets(part: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        replacement = f"{match.group(1)}{match.group(2)}***"
        if "bearer" in value.lower():
            replacement = f"{match.group(1)}{match.group(2)}Bearer ***"
        return replacement

    return _ARCHIVE_SECRET_VALUE_RE.sub(replace, part)


def _sanitize_archive_part(part: str) -> str:
    part = _redact_archive_part_secrets(part)
    sanitized = "".join(
        "_"
        if char in {":", "?", "#"} or ord(char) < 32 or ord(char) == 127
        else char
        for char in part
    )
    return _bound_archive_part(sanitized)


def _bound_archive_parts(parts: list[str]) -> list[str]:
    if not parts:
        return parts

    archive_path = "/".join(parts)
    if len(archive_path) <= MAX_ARCHIVE_PATH_CHARS:
        return parts

    category = "unsafe" if parts[0] == "unsafe" else "truncated"
    digest = _stable_digest(archive_path)
    leaf = parts[-1] or "file"
    return [category, _bound_archive_part(f"{digest}_{leaf}")]


def _safe_archive_parts(path: str) -> list[str]:
    normalized = str(path or "").replace("\\", "/").strip()
    parts = [
        part
        for part in PurePosixPath(normalized).parts
        if part not in {"", ".", "/"}
    ]

    if not parts:
        return []
    if parts and all(part != ".." for part in parts):
        return [_sanitize_archive_part(part) for part in parts]

    leaf = next((part for part in reversed(parts) if part not in {".."}), "file")
    leaf = _sanitize_archive_part(leaf) or "file"
    digest = _stable_digest(normalized)
    return ["unsafe", _bound_archive_part(f"{digest}_{leaf}")]


def safe_archive_path(path: str, *, prefix: str | None = None) -> str:
    parts: list[str] = []
    if prefix:
        parts.extend(_safe_archive_parts(prefix))
    path_parts = _safe_archive_parts(path)
    parts.extend(path_parts or ["file"])
    return "/".join(_bound_archive_parts(parts))


def dedupe_archive_path(archive_path: str, seen: set[str]) -> str:
    archive_path = safe_archive_path(archive_path)
    if archive_path not in seen:
        seen.add(archive_path)
        return archive_path

    path = PurePosixPath(archive_path)
    stem = path.stem or "file"
    suffix = path.suffix
    parent_parts = [
        part for part in path.parent.parts if part not in {"", ".", "/"}
    ]
    index = 2
    while True:
        filename = f"{stem}-{index}{suffix}"
        candidate = "/".join(_bound_archive_parts([*parent_parts, filename]))
        if candidate not in seen:
            seen.add(candidate)
            return candidate
        index += 1


def _safe_artifact_stem(value: object, *, suffix: str = "") -> str:
    candidate = Path(str(value or "")).name
    if suffix and candidate.endswith(suffix):
        candidate = candidate[: -len(suffix)]
    candidate = _redact_archive_part_secrets(candidate)
    candidate = re.sub(r"(?i)(\s*[:=]\s*)Bearer\s+\*\*\*", r"\1***", candidate)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._")


def safe_artifact_name(
    name: str | None,
    *,
    default: str,
    suffix: str = "",
) -> str:
    candidate = _safe_artifact_stem(name, suffix=suffix)
    if not candidate:
        candidate = _safe_artifact_stem(default, suffix=suffix) or "artifact"
    if len(candidate) > MAX_ARTIFACT_STEM_CHARS:
        digest = hashlib.sha1(candidate.encode("utf-8")).hexdigest()[:8]
        candidate = f"{candidate[: MAX_ARTIFACT_STEM_CHARS - 9]}_{digest}"
    return f"{candidate}{suffix}"
