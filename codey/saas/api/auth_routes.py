from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.cookies import clear_auth_cookie, set_auth_cookie
from codey.saas.auth.oauth import decode_oauth_state, oauth_github_url, oauth_google_url
from codey.saas.auth.public_urls import (
    API_BASE_URL_HEADER,
    FRONTEND_ORIGIN_HEADER,
    get_public_api_base_url,
    get_public_frontend_origin,
)
from codey.saas.auth.service import AuthService
from codey.saas.config import settings
from codey.saas.database import get_db
from codey.saas.security.audit import AuditLogger, ACTION_LOGIN_SUCCESS, ACTION_LOGIN_FAILURE

router = APIRouter(prefix="/auth", tags=["auth"])
_AUTH_ALLOWED_SCHEMES = {"http", "https"}
_BCRYPT_MAX_PASSWORD_BYTES = 72
_AUTH_URL_CREDENTIALS_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_AUTH_QUERY_SECRET_RE = re.compile(
    r"([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token)=)[^&#\s]+",
    re.IGNORECASE,
)
_AUTH_URL_SECRET_PARAM_RE = re.compile(
    r"(?:^|[&;])(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|password|secret|token)=",
    re.IGNORECASE,
)
_AUTH_NAMED_SECRET_RE = re.compile(
    r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token|authorization)\b(\s*[:=]\s*)"
    r"(?:Bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
_AUTH_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)


def _validate_auth_password_not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _validate_bcrypt_password(value: str) -> str:
    value = _validate_auth_password_not_blank(value)
    if len(value.encode("utf-8")) > _BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError("must be 72 bytes or fewer")
    return value


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None

    @field_validator("password")
    @classmethod
    def _validate_non_blank_password(cls, value: str) -> str:
        return _validate_bcrypt_password(value)

    @field_validator("name")
    @classmethod
    def _normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def _validate_non_blank_password(cls, value: str) -> str:
        return _validate_auth_password_not_blank(value)


class ResetPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordConfirmRequest(BaseModel):
    token: str
    password: str

    @field_validator("token")
    @classmethod
    def _strip_and_validate_token(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("password")
    @classmethod
    def _validate_non_blank_password(cls, value: str) -> str:
        return _validate_bcrypt_password(value)


class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None
    avatar_url: str | None
    github_connected: bool
    plan: str
    plan_status: str
    credits_remaining: int
    topup_credits: int
    total_credits: int
    created_at: str


class AuthResponse(BaseModel):
    user: UserResponse
    token: str


class OAuthUrlResponse(BaseModel):
    url: str
    state: str


class OAuthProvidersResponse(BaseModel):
    github: bool
    google: bool


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_auth_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _coerce_non_empty_auth_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_auth_bearer_token(value: object) -> str | None:
    token = _coerce_non_empty_auth_text(value)
    if token is None or _has_ascii_control(token) or _has_whitespace(token):
        return None
    return token


def _coerce_auth_avatar_url(value: object) -> str | None:
    url = _coerce_non_empty_auth_text(value)
    if url is None or _has_ascii_control(url):
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in _AUTH_ALLOWED_SCHEMES:
        return None
    if port is not None and not (1 <= port <= 65535):
        return None
    if not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    if _AUTH_URL_SECRET_PARAM_RE.search(parsed.query):
        return None
    if _AUTH_URL_SECRET_PARAM_RE.search(parsed.fragment):
        return None
    return url


def _redact_auth_error(value: object) -> str:
    text = _AUTH_URL_CREDENTIALS_RE.sub(r"\1***@", str(value))
    text = _AUTH_QUERY_SECRET_RE.sub(r"\1***", text)

    def _replace_named_secret(match: re.Match[str]) -> str:
        prefix = f"{match.group(1)}{match.group(2)}"
        if "bearer" in match.group(0).lower():
            return f"{prefix}Bearer ***"
        return f"{prefix}***"

    text = _AUTH_NAMED_SECRET_RE.sub(_replace_named_secret, text)
    return _AUTH_EMAIL_RE.sub("[redacted-email]", text)


def _coerce_auth_int(value: object, fallback: int = 0) -> int:
    normalized: float
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        normalized = value
    elif isinstance(value, str):
        try:
            normalized = float(value.strip())
        except ValueError:
            return fallback
    else:
        return fallback
    return int(normalized) if math.isfinite(normalized) else fallback


def _normalize_auth_frontend_origin(value: object) -> str | None:
    normalized = _coerce_non_empty_auth_text(value)
    if normalized is None:
        return None
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None

    parsed = urlparse(normalized)
    if (
        parsed.scheme not in _AUTH_ALLOWED_SCHEMES
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


def _normalize_auth_api_base_url(value: object) -> str | None:
    normalized = _coerce_non_empty_auth_text(value)
    if normalized is None:
        return None
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None

    parsed = urlparse(normalized)
    if (
        parsed.scheme not in _AUTH_ALLOWED_SCHEMES
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


def _resolved_oauth_callback_frontend_origin(
    request: Request,
    state_data: dict[str, object],
) -> str:
    return _normalize_auth_frontend_origin(
        state_data.get("frontend_origin")
    ) or get_public_frontend_origin(request)


def _resolved_oauth_callback_api_base_url(
    request: Request,
    state_data: dict[str, object],
) -> str:
    return _normalize_auth_api_base_url(
        state_data.get("api_base_url")
    ) or get_public_api_base_url(request)


def _has_github_connection(user: object) -> bool:
    return bool(
        _coerce_non_empty_auth_text(getattr(user, "github_id", None))
        or _coerce_auth_bearer_token(getattr(user, "github_token", None))
    )


def _user_to_response(user) -> UserResponse:
    credits_remaining = _coerce_auth_int(getattr(user, "credits_remaining", None), 0)
    topup_credits = _coerce_auth_int(getattr(user, "topup_credits", None), 0)
    total_credits = _coerce_auth_int(
        getattr(user, "total_credits", None),
        credits_remaining + topup_credits,
    )
    return UserResponse(
        id=str(getattr(user, "id", "")),
        email=_coerce_non_empty_auth_text(getattr(user, "email", None)) or "",
        name=_coerce_non_empty_auth_text(getattr(user, "name", None)),
        avatar_url=_coerce_auth_avatar_url(getattr(user, "avatar_url", None)),
        github_connected=_has_github_connection(user),
        plan=(
            _coerce_non_empty_auth_text(getattr(user, "plan", None)) or "free"
        ).lower(),
        plan_status=(
            _coerce_non_empty_auth_text(getattr(user, "plan_status", None)) or "active"
        ),
        credits_remaining=credits_remaining,
        topup_credits=topup_credits,
        total_credits=total_credits,
        created_at=_serialize_auth_timestamp(getattr(user, "created_at", None)) or "",
    )


def _browser_callback_redirect(
    provider: str,
    state: str,
    *,
    frontend_origin: str | None = None,
) -> str:
    frontend_base_url = (
        _normalize_auth_frontend_origin(frontend_origin)
        or _normalize_auth_frontend_origin(settings.frontend_url)
        or ""
    ).rstrip("/")
    return (
        f"{frontend_base_url}/auth/callback?"
        f"{urlencode({'provider': provider, 'state': state, 'auth_complete': '1'})}"
    )


def _wants_browser_redirect(request: Request) -> bool:
    if request.headers.get(FRONTEND_ORIGIN_HEADER) or request.headers.get(API_BASE_URL_HEADER):
        return False

    accept = request.headers.get("accept", "").lower()
    sec_fetch_dest = request.headers.get("sec-fetch-dest", "").lower()
    sec_fetch_mode = request.headers.get("sec-fetch-mode", "").lower()
    user_agent = request.headers.get("user-agent", "").lower()

    if sec_fetch_dest == "document" or sec_fetch_mode == "navigate":
        return True
    if "text/html" in accept:
        return True
    if "mozilla/" in user_agent and accept.strip() in {"", "*/*"}:
        return True
    return False


def _github_oauth_configured() -> bool:
    return bool(
        _coerce_non_empty_auth_text(settings.github_client_id)
        and _coerce_non_empty_auth_text(settings.github_client_secret)
    )


def _google_oauth_configured() -> bool:
    return bool(
        _coerce_non_empty_auth_text(settings.google_client_id)
        and _coerce_non_empty_auth_text(settings.google_client_secret)
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    auth_service = AuthService(db)
    user, token = await auth_service.signup(
        email=body.email,
        password=body.password,
        name=body.name,
        frontend_origin=get_public_frontend_origin(request),
    )
    set_auth_cookie(
        response,
        token,
        frontend_origin=get_public_frontend_origin(request),
        api_base_url=get_public_api_base_url(request),
    )
    return AuthResponse(user=_user_to_response(user), token=token)


@router.post("/login", response_model=AuthResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    auth_service = AuthService(db)
    audit = AuditLogger(db)
    try:
        user, token = await auth_service.login(email=body.email, password=body.password)
        set_auth_cookie(
            response,
            token,
            frontend_origin=get_public_frontend_origin(request),
            api_base_url=get_public_api_base_url(request),
        )
        await audit.log(user_id=user.id, action=ACTION_LOGIN_SUCCESS, result="success")
        return AuthResponse(user=_user_to_response(user), token=token)
    except HTTPException:
        await audit.log(
            user_id=None,
            action=ACTION_LOGIN_FAILURE,
            result="failure",
            failure_reason=f"Invalid credentials for {body.email}",
        )
        raise


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    clear_auth_cookie(
        response,
        frontend_origin=get_public_frontend_origin(request),
        api_base_url=get_public_api_base_url(request),
    )


@router.get("/providers", response_model=OAuthProvidersResponse)
async def oauth_providers() -> OAuthProvidersResponse:
    return OAuthProvidersResponse(
        github=_github_oauth_configured(),
        google=_google_oauth_configured(),
    )


@router.get("/github", response_model=OAuthUrlResponse)
async def github_redirect(
    request: Request,
    intent: str = "login",
) -> OAuthUrlResponse:
    if not _github_oauth_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub sign-in is not configured.",
        )
    if intent not in {"login", "connect"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub OAuth intent",
        )
    url, state = oauth_github_url(
        intent=intent,
        frontend_origin=get_public_frontend_origin(request),
        api_base_url=get_public_api_base_url(request),
    )
    return OAuthUrlResponse(url=url, state=state)


@router.get("/github/callback", response_model=AuthResponse)
async def github_callback(
    code: str,
    state: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse | RedirectResponse:
    if not _github_oauth_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub sign-in is not configured.",
        )
    try:
        state_data = decode_oauth_state(state, "github")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_redact_auth_error(exc),
        ) from exc
    auth_service = AuthService(db)
    user, token = await auth_service.github_callback(code, state)
    frontend_origin = _resolved_oauth_callback_frontend_origin(request, state_data)
    api_base_url = _resolved_oauth_callback_api_base_url(request, state_data)
    if _wants_browser_redirect(request):
        redirect = RedirectResponse(
            url=_browser_callback_redirect(
                "github",
                state,
                frontend_origin=frontend_origin,
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        set_auth_cookie(
            redirect,
            token,
            frontend_origin=frontend_origin,
            api_base_url=api_base_url,
        )
        return redirect
    set_auth_cookie(
        response,
        token,
        frontend_origin=frontend_origin,
        api_base_url=api_base_url,
    )
    return AuthResponse(user=_user_to_response(user), token=token)


@router.get("/google", response_model=OAuthUrlResponse)
async def google_redirect(request: Request) -> OAuthUrlResponse:
    if not _google_oauth_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured.",
        )
    url, state = oauth_google_url(
        frontend_origin=get_public_frontend_origin(request),
        api_base_url=get_public_api_base_url(request),
    )
    return OAuthUrlResponse(url=url, state=state)


@router.get("/google/callback", response_model=AuthResponse)
async def google_callback(
    code: str,
    state: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse | RedirectResponse:
    if not _google_oauth_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured.",
        )
    try:
        state_data = decode_oauth_state(state, "google")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_redact_auth_error(exc),
        ) from exc
    auth_service = AuthService(db)
    user, token = await auth_service.google_callback(code, state)
    frontend_origin = _resolved_oauth_callback_frontend_origin(request, state_data)
    api_base_url = _resolved_oauth_callback_api_base_url(request, state_data)
    if _wants_browser_redirect(request):
        redirect = RedirectResponse(
            url=_browser_callback_redirect(
                "google",
                state,
                frontend_origin=frontend_origin,
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        set_auth_cookie(
            redirect,
            token,
            frontend_origin=frontend_origin,
            api_base_url=api_base_url,
        )
        return redirect
    set_auth_cookie(
        response,
        token,
        frontend_origin=frontend_origin,
        api_base_url=api_base_url,
    )
    return AuthResponse(user=_user_to_response(user), token=token)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth_service = AuthService(db)
    await auth_service.request_password_reset(
        body.email,
        frontend_origin=get_public_frontend_origin(request),
    )
    return MessageResponse(
        message="If an account with that email exists, a reset link has been sent."
    )


@router.post("/reset-password/confirm", response_model=MessageResponse)
async def reset_password_confirm(
    body: ResetPasswordConfirmRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth_service = AuthService(db)
    success = await auth_service.reset_password(body.token, body.password)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    return MessageResponse(message="Password has been reset successfully.")
