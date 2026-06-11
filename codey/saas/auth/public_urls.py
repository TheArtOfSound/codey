from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse, urlunparse

if TYPE_CHECKING:
    from fastapi import Request

from codey.saas.config import settings

FRONTEND_ORIGIN_HEADER = "x-codey-frontend-origin"
API_BASE_URL_HEADER = "x-codey-api-base-url"
_ALLOWED_SCHEMES = {"http", "https"}


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_url_fallback(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().rstrip("/")


def _normalize_frontend_origin(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if _has_ascii_control(value) or _has_whitespace(value):
        return None

    parsed = urlparse(value)
    if (
        parsed.scheme not in _ALLOWED_SCHEMES
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and port <= 0:
        return None

    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")


def _normalize_api_base_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if _has_ascii_control(value) or _has_whitespace(value):
        return None

    parsed = urlparse(value)
    if (
        parsed.scheme not in _ALLOWED_SCHEMES
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and port <= 0:
        return None

    path = parsed.path.replace("\\", "/").rstrip("/")
    decoded_path = unquote(parsed.path).replace("\\", "/").rstrip("/")
    if any(part == ".." for part in decoded_path.split("/")):
        return None
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def get_public_frontend_origin(request: Request | None = None) -> str:
    if request is not None:
        origin = _normalize_frontend_origin(request.headers.get(FRONTEND_ORIGIN_HEADER))
        if origin:
            return origin

    return _normalize_frontend_origin(settings.frontend_url) or ""


def get_public_api_base_url(request: Request | None = None) -> str:
    if request is not None:
        base_url = _normalize_api_base_url(request.headers.get(API_BASE_URL_HEADER))
        if base_url:
            return base_url

    return _normalize_api_base_url(settings.api_url) or ""
