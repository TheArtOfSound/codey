from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
import secrets
from typing import Any, Literal
from urllib.parse import unquote, urlencode, urlparse, urlunparse

try:
    import httpx
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-light tests
    if exc.name != "httpx":
        raise
    _HTTPX_IMPORT_ERROR: ModuleNotFoundError | None = exc
    httpx: Any = None
else:  # pragma: no cover - depends on optional runtime dependency
    _HTTPX_IMPORT_ERROR = None

try:
    from fastapi import HTTPException
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-light tests
    if exc.name != "fastapi":
        raise
    _FASTAPI_IMPORT_ERROR: ModuleNotFoundError | None = exc

    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(
            self,
            status_code: int,
            detail: Any = None,
            headers: dict[str, str] | None = None,
        ) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail
            self.headers = headers

else:  # pragma: no cover - depends on optional runtime dependency
    _FASTAPI_IMPORT_ERROR = None

try:
    from jose import JWTError, jwt
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-light tests
    if exc.name != "jose":
        raise
    _JOSE_IMPORT_ERROR: ModuleNotFoundError | None = exc

    class JWTError(Exception):  # type: ignore[no-redef]
        pass

    jwt: Any = None
else:  # pragma: no cover - depends on optional runtime dependency
    _JOSE_IMPORT_ERROR = None

from codey.saas.config import settings

# ---------------------------------------------------------------------------
# GitHub OAuth
# ---------------------------------------------------------------------------

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"
_GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
_OAUTH_STATE_TTL = timedelta(minutes=10)
_OAUTH_HTTP_TIMEOUT = 20.0
GitHubOAuthIntent = Literal["login", "connect"]
_OAUTH_ALLOWED_SCHEMES = {"http", "https"}
_OAUTH_URL_SECRET_PARAM_RE = re.compile(
    r"(?:^|[&;])(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|password|secret|token)=",
    re.IGNORECASE,
)


def _require_httpx() -> None:
    if _HTTPX_IMPORT_ERROR is not None:
        raise RuntimeError("httpx is required for OAuth provider calls") from _HTTPX_IMPORT_ERROR


def _require_jose() -> None:
    if _JOSE_IMPORT_ERROR is not None:
        raise RuntimeError("python-jose is required for OAuth state auth") from _JOSE_IMPORT_ERROR


def _coerce_non_empty_oauth_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if _has_ascii_control(normalized):
        return None
    return normalized or None


def _oauth_setting_text(value: Any) -> str:
    return _coerce_non_empty_oauth_text(value) or ""


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_oauth_avatar_url(value: Any) -> str | None:
    url = _coerce_non_empty_oauth_text(value)
    if url is None:
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in _OAUTH_ALLOWED_SCHEMES:
        return None
    if port is not None and not (1 <= port <= 65535):
        return None
    if not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    if _OAUTH_URL_SECRET_PARAM_RE.search(parsed.query):
        return None
    if _OAUTH_URL_SECRET_PARAM_RE.search(parsed.fragment):
        return None
    return url


def _coerce_oauth_bearer_token(value: Any) -> str | None:
    normalized = _coerce_non_empty_oauth_text(value)
    if normalized is None or _has_whitespace(normalized):
        return None
    return normalized


def _oauth_response_json(response: Any, provider: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"{provider} OAuth failed. Try again.",
        ) from exc


def _require_oauth_code(code: object, provider: str) -> str:
    normalized = _coerce_non_empty_oauth_text(code)
    if normalized is None:
        raise HTTPException(
            status_code=400,
            detail=f"{provider} OAuth failed: missing authorization code",
        )
    return normalized


def _coerce_oauth_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
        return fallback
    if isinstance(value, float):
        if not math.isfinite(value):
            return fallback
        if value == 1.0:
            return True
        if value == 0.0:
            return False
        return fallback
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
    return fallback


def _coerce_oauth_numeric_date(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        timestamp = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        timestamp = int(value)
    else:
        return None
    if timestamp < 0:
        return None
    return timestamp


def _normalize_oauth_api_base_url(value: object) -> str | None:
    normalized = _coerce_non_empty_oauth_text(value)
    if normalized is None:
        return None
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None

    parsed = urlparse(normalized)
    if (
        parsed.scheme not in _OAUTH_ALLOWED_SCHEMES
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


def _normalize_oauth_frontend_origin(value: object) -> str | None:
    normalized = _coerce_non_empty_oauth_text(value)
    if normalized is None:
        return None
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None

    parsed = urlparse(normalized)
    if (
        parsed.scheme not in _OAUTH_ALLOWED_SCHEMES
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


def _build_callback_url(provider: str, api_base_url: str | None = None) -> str:
    base_url = (
        _normalize_oauth_api_base_url(api_base_url)
        or _normalize_oauth_api_base_url(settings.api_url)
        or ""
    ).rstrip("/")
    return f"{base_url}/auth/{provider}/callback"


def _create_oauth_state(
    provider: str,
    *,
    frontend_origin: str | None = None,
    api_base_url: str | None = None,
    intent: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": "oauth_state",
        "provider": provider,
        "nonce": secrets.token_urlsafe(24),
        "iat": int(now.timestamp()),
        "exp": int((now + _OAUTH_STATE_TTL).timestamp()),
    }
    normalized_frontend_origin = _normalize_oauth_frontend_origin(frontend_origin)
    if normalized_frontend_origin:
        payload["frontend_origin"] = normalized_frontend_origin
    normalized_api_base_url = _normalize_oauth_api_base_url(api_base_url)
    if normalized_api_base_url:
        payload["api_base_url"] = normalized_api_base_url
    if intent:
        payload["intent"] = intent
    _require_jose()
    assert jwt is not None
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_oauth_state(state: str, provider: str) -> dict[str, Any]:
    normalized_state = _coerce_non_empty_oauth_text(state)
    if normalized_state is None:
        raise ValueError("Invalid or expired OAuth state")
    _require_jose()
    assert jwt is not None
    try:
        payload = jwt.decode(
            normalized_state,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise ValueError("Invalid or expired OAuth state") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid OAuth state")

    if payload.get("purpose") != "oauth_state" or payload.get("provider") != provider:
        raise ValueError("Invalid OAuth state")

    issued_at = _coerce_oauth_numeric_date(payload.get("iat"))
    expires_at = _coerce_oauth_numeric_date(payload.get("exp"))
    nonce = _coerce_non_empty_oauth_text(payload.get("nonce"))
    if (
        issued_at is None
        or expires_at is None
        or expires_at <= issued_at
        or nonce is None
    ):
        raise ValueError("Invalid OAuth state")

    payload["nonce"] = nonce
    payload["iat"] = issued_at
    payload["exp"] = expires_at
    return payload


def validate_oauth_state(state: str, provider: str) -> None:
    decode_oauth_state(state, provider)


def _github_scope(intent: GitHubOAuthIntent) -> str:
    if intent == "connect":
        return "read:user user:email repo read:org"
    if intent == "login":
        return "read:user user:email"
    raise ValueError("Invalid GitHub OAuth intent")


def oauth_github_url(
    *,
    intent: GitHubOAuthIntent = "login",
    frontend_origin: str | None = None,
    api_base_url: str | None = None,
) -> tuple[str, str]:
    """Return the full GitHub OAuth authorization URL."""
    scope = _github_scope(intent)
    state = _create_oauth_state(
        "github",
        frontend_origin=frontend_origin,
        api_base_url=api_base_url,
        intent=intent,
    )
    params = {
        "client_id": _oauth_setting_text(settings.github_client_id),
        "redirect_uri": _build_callback_url("github", api_base_url),
        "scope": scope,
        "state": state,
    }
    return f"{_GITHUB_AUTHORIZE_URL}?{urlencode(params)}", state


async def exchange_github_code(code: str, redirect_uri: str | None = None) -> dict:
    """Exchange a GitHub OAuth code for an access token and fetch user info.

    Returns a dict with keys: ``id``, ``email``, ``name``, ``avatar_url``,
    ``access_token``.
    """
    code = _require_oauth_code(code, "GitHub")
    _require_httpx()
    assert httpx is not None
    try:
        async with httpx.AsyncClient(timeout=_OAUTH_HTTP_TIMEOUT) as client:
            # Exchange code for access token
            token_request = {
                "client_id": _oauth_setting_text(settings.github_client_id),
                "client_secret": _oauth_setting_text(settings.github_client_secret),
                "code": code,
            }
            if redirect_uri:
                token_request["redirect_uri"] = redirect_uri

            token_resp = await client.post(
                _GITHUB_TOKEN_URL,
                data=token_request,
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            token_data = _oauth_response_json(token_resp, "GitHub")
            if not isinstance(token_data, dict):
                raise HTTPException(status_code=502, detail="GitHub OAuth failed. Try again.")
            error_code = _coerce_non_empty_oauth_text(token_data.get("error"))
            error_description = _coerce_non_empty_oauth_text(token_data.get("error_description"))
            if error_code or error_description:
                description = error_description or error_code or "authorization failed"
                raise HTTPException(status_code=400, detail=f"GitHub OAuth failed: {description}")
            access_token = _coerce_oauth_bearer_token(token_data.get("access_token"))
            if not access_token:
                raise HTTPException(status_code=502, detail="GitHub OAuth failed. Try again.")

            auth_headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }

            # Fetch user profile
            user_resp = await client.get(_GITHUB_USER_URL, headers=auth_headers)
            user_resp.raise_for_status()
            user_data = _oauth_response_json(user_resp, "GitHub")
            if not isinstance(user_data, dict):
                raise HTTPException(status_code=502, detail="GitHub OAuth failed. Try again.")

            # If email is not public, fetch from the emails endpoint
            email = _coerce_non_empty_oauth_text(user_data.get("email"))
            if not email:
                emails_resp = await client.get(_GITHUB_EMAILS_URL, headers=auth_headers)
                emails_resp.raise_for_status()
                emails = _oauth_response_json(emails_resp, "GitHub")
                if not isinstance(emails, list):
                    raise HTTPException(status_code=502, detail="GitHub OAuth failed. Try again.")
                primary = next(
                    (
                        e
                        for e in emails
                        if isinstance(e, dict)
                        and _coerce_oauth_bool(e.get("primary"))
                        and _coerce_oauth_bool(e.get("verified"))
                    ),
                    None,
                )
                if primary:
                    email = _coerce_non_empty_oauth_text(primary.get("email"))
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=f"GitHub OAuth timed out after {_OAUTH_HTTP_TIMEOUT:.0f}s",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail="GitHub OAuth failed. Try again.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="GitHub OAuth failed. Try again.",
        ) from exc

    user_id_value = user_data.get("id")
    if not isinstance(user_id_value, (str, int)) or isinstance(user_id_value, bool):
        raise HTTPException(status_code=502, detail="GitHub OAuth failed. Try again.")
    user_id = _coerce_non_empty_oauth_text(str(user_id_value))
    if user_id is None:
        raise HTTPException(status_code=502, detail="GitHub OAuth failed. Try again.")

    name = _coerce_non_empty_oauth_text(user_data.get("name"))
    if not name:
        name = _coerce_non_empty_oauth_text(user_data.get("login"))

    avatar_url = _coerce_oauth_avatar_url(user_data.get("avatar_url"))

    return {
        "id": user_id,
        "email": email,
        "name": name,
        "avatar_url": avatar_url,
        "access_token": access_token,
    }


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------

_GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def oauth_google_url(
    *,
    frontend_origin: str | None = None,
    api_base_url: str | None = None,
) -> tuple[str, str]:
    """Return the full Google OAuth authorization URL."""
    state = _create_oauth_state(
        "google",
        frontend_origin=frontend_origin,
        api_base_url=api_base_url,
    )
    params = {
        "client_id": _oauth_setting_text(settings.google_client_id),
        "redirect_uri": _build_callback_url("google", api_base_url),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{_GOOGLE_AUTHORIZE_URL}?{urlencode(params)}", state


async def exchange_google_code(code: str, redirect_uri: str | None = None) -> dict:
    """Exchange a Google OAuth code for an access token and fetch user info.

    Returns a dict with keys: ``id``, ``email``, ``name``, ``avatar_url``,
    ``access_token``.
    """
    code = _require_oauth_code(code, "Google")
    _require_httpx()
    assert httpx is not None
    try:
        async with httpx.AsyncClient(timeout=_OAUTH_HTTP_TIMEOUT) as client:
            # Exchange code for tokens
            token_resp = await client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "client_id": _oauth_setting_text(settings.google_client_id),
                    "client_secret": _oauth_setting_text(settings.google_client_secret),
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri or _build_callback_url("google"),
                },
            )
            token_resp.raise_for_status()
            token_data = _oauth_response_json(token_resp, "Google")
            if not isinstance(token_data, dict):
                raise HTTPException(status_code=502, detail="Google OAuth failed. Try again.")
            error_code = _coerce_non_empty_oauth_text(token_data.get("error"))
            error_description = _coerce_non_empty_oauth_text(token_data.get("error_description"))
            if error_code or error_description:
                description = error_description or error_code or "authorization failed"
                raise HTTPException(status_code=400, detail=f"Google OAuth failed: {description}")
            access_token = _coerce_oauth_bearer_token(token_data.get("access_token"))
            if not access_token:
                raise HTTPException(status_code=502, detail="Google OAuth failed. Try again.")

            # Fetch user profile
            user_resp = await client.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_resp.raise_for_status()
            user_data = _oauth_response_json(user_resp, "Google")
            if not isinstance(user_data, dict):
                raise HTTPException(status_code=502, detail="Google OAuth failed. Try again.")
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=f"Google OAuth timed out after {_OAUTH_HTTP_TIMEOUT:.0f}s",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail="Google OAuth failed. Try again.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="Google OAuth failed. Try again.",
        ) from exc

    user_id = _coerce_non_empty_oauth_text(user_data.get("id"))
    if not user_id:
        raise HTTPException(status_code=502, detail="Google OAuth failed. Try again.")

    email = _coerce_non_empty_oauth_text(user_data.get("email"))
    if not email:
        raise HTTPException(status_code=502, detail="Google OAuth failed. Try again.")

    name = _coerce_non_empty_oauth_text(user_data.get("name"))

    avatar_url = _coerce_oauth_avatar_url(user_data.get("picture"))

    return {
        "id": user_id,
        "email": email,
        "name": name,
        "avatar_url": avatar_url,
        "access_token": access_token,
    }
