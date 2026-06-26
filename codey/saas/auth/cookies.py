from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse, urlunparse

if TYPE_CHECKING:
    from fastapi import Response

from codey.saas.auth.public_urls import get_public_api_base_url, get_public_frontend_origin
from codey.saas.config import settings

SESSION_COOKIE_NAME = "codey_session"
_MULTIPART_SUFFIX_PREFIXES = {"ac", "co", "com", "edu", "gov", "net", "org"}
_COOKIE_ALLOWED_SCHEMES = {"http", "https"}


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_session_cookie_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or _has_ascii_control(normalized)
        or _has_whitespace(normalized)
    ):
        return None
    return normalized


def _is_https_url(value: str | None) -> bool:
    return urlparse(value or "").scheme == "https"


def _cookie_domain_from_hostname(hostname: str | None) -> str | None:
    if not hostname:
        return None
    hostname = hostname.rstrip(".").lower()
    if not hostname:
        return None

    if hostname in {"localhost", "127.0.0.1"}:
        return None

    try:
        ipaddress.ip_address(hostname)
        return None
    except ValueError:
        pass

    parts = hostname.split(".")
    if len(parts) < 2:
        return None

    if (
        len(parts) == 2
        and len(parts[-1]) == 2
        and parts[-2] in _MULTIPART_SUFFIX_PREFIXES
    ):
        return None

    if (
        len(parts) >= 3
        and len(parts[-1]) == 2
        and parts[-2] in _MULTIPART_SUFFIX_PREFIXES
    ):
        return "." + ".".join(parts[-3:])

    return "." + ".".join(parts[-2:])


def _normalize_cookie_frontend_origin(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None

    parsed = urlparse(normalized)
    if (
        parsed.scheme not in _COOKIE_ALLOWED_SCHEMES
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


def _normalize_cookie_api_base_url(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None

    parsed = urlparse(normalized)
    if (
        parsed.scheme not in _COOKIE_ALLOWED_SCHEMES
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


def _shared_cookie_domain(
    frontend_origin: str | None = None,
    api_base_url: str | None = None,
) -> str | None:
    frontend_hostname = urlparse(_normalize_cookie_frontend_origin(frontend_origin) or "").hostname
    api_hostname = urlparse(_normalize_cookie_api_base_url(api_base_url) or "").hostname

    if frontend_hostname and api_hostname:
        frontend_domain = _cookie_domain_from_hostname(frontend_hostname)
        api_domain = _cookie_domain_from_hostname(api_hostname)
        if frontend_domain == api_domain:
            return frontend_domain
        return None

    for hostname in (
        frontend_hostname,
        api_hostname,
        urlparse(get_public_frontend_origin()).hostname,
        urlparse(get_public_api_base_url()).hostname,
    ):
        domain = _cookie_domain_from_hostname(hostname)
        if domain is not None:
            return domain

    return None


def _cookie_is_secure(
    frontend_origin: str | None = None,
    api_base_url: str | None = None,
) -> bool:
    normalized_api_base_url = _normalize_cookie_api_base_url(api_base_url)
    if normalized_api_base_url:
        return _is_https_url(normalized_api_base_url)
    normalized_frontend_origin = _normalize_cookie_frontend_origin(frontend_origin)
    if normalized_frontend_origin:
        return _is_https_url(normalized_frontend_origin)

    return _is_https_url(get_public_api_base_url()) or _is_https_url(get_public_frontend_origin())


def set_auth_cookie(
    response: Response,
    token: str,
    *,
    frontend_origin: str | None = None,
    api_base_url: str | None = None,
) -> None:
    cookie_value = _coerce_session_cookie_value(token)
    if cookie_value is None:
        raise ValueError("Session cookie token cannot be empty")
    max_age = settings.jwt_expire_minutes * 60
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie_value,
        max_age=max_age,
        expires=max_age,
        httponly=True,
        secure=_cookie_is_secure(frontend_origin, api_base_url),
        samesite="lax",
        path="/",
        domain=_shared_cookie_domain(frontend_origin, api_base_url),
    )


def clear_auth_cookie(
    response: Response,
    *,
    frontend_origin: str | None = None,
    api_base_url: str | None = None,
) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        domain=_shared_cookie_domain(frontend_origin, api_base_url),
        httponly=True,
        secure=_cookie_is_secure(frontend_origin, api_base_url),
        samesite="lax",
    )
