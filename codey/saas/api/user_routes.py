from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

import bcrypt
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user, require_plan
from codey.saas.billing.plans import PLANS
from codey.saas.billing.service import BillingError, BillingService
from codey.saas.credits.service import CreditService, PLAN_CREDITS
from codey.saas.database import get_db
from codey.saas.models import (
    ApiKey,
    BuildCheckpoint,
    BuildFile,
    BuildProject,
    CodingSession,
    CreditTransaction,
    Export,
    MemoryUpdateLog,
    Project,
    ProjectVersion,
    Referral,
    Repository,
    SecurityAuditLog,
    SessionCost,
    User,
    UserMemory,
)
from codey.saas.security.audit import (
    ACTION_API_KEY_CREATE,
    ACTION_API_KEY_DELETE,
    AuditLogger,
)
from codey.saas.security.encryption import generate_api_key

router = APIRouter(prefix="/users", tags=["users"])
_GITHUB_API_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_GITHUB_USER_URL = "https://api.github.com/user"
_GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
_BCRYPT_MAX_PASSWORD_BYTES = 72
_USER_ALLOWED_AVATAR_SCHEMES = {"http", "https"}
_USER_URL_SECRET_PARAM_RE = re.compile(
    r"(?:^|[&;])(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|password|secret|token)=",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UserProfileResponse(BaseModel):
    id: str
    email: str
    name: str | None
    avatar_url: str | None
    github_connected: bool
    plan: str
    plan_display_name: str
    plan_status: str
    credits_remaining: int
    topup_credits: int
    total_credits: int
    credits_used_this_month: int
    monthly_allocation: int
    subscription_period_end: str | None
    created_at: str
    last_active: str | None


class UpdateUserRequest(BaseModel):
    name: str | None = None
    avatar_url: str | None = None

    @field_validator("name")
    @classmethod
    def _strip_and_validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("avatar_url")
    @classmethod
    def _strip_and_validate_avatar_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        url = _coerce_user_avatar_url(value)
        if url is None:
            raise ValueError("must be a public HTTP(S) URL")
        return url


class DeleteUserRequest(BaseModel):
    confirm: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("current_password", "new_password")
    @classmethod
    def _validate_password_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("new_password")
    @classmethod
    def _validate_new_password_bcrypt_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > _BCRYPT_MAX_PASSWORD_BYTES:
            raise ValueError("must be 72 bytes or fewer")
        return value


class ConnectGitHubTokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=2048)

    @field_validator("token")
    @classmethod
    def _strip_and_validate_token(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        if _has_ascii_control(value) or _has_whitespace(value):
            raise ValueError("must not contain whitespace or control characters")
        if len(value) < 20:
            raise ValueError("must be at least 20 characters")
        return value


class CreditBalanceResponse(BaseModel):
    subscription_credits: int
    topup_credits: int
    total: int
    used_this_month: int
    plan: str
    monthly_allocation: int


class SessionSummary(BaseModel):
    id: str
    mode: str
    prompt: str | None
    repo_connected: str | None
    status: str
    credits_charged: int
    lines_generated: int
    files_modified: int
    nfet_phase_before: str | None
    nfet_phase_after: str | None
    es_score_before: float | None
    es_score_after: float | None
    output_summary: str | None
    error_message: str | None
    started_at: str
    completed_at: str | None


class PaginatedSessionsResponse(BaseModel):
    sessions: list[SessionSummary]
    total: int
    limit: int
    offset: int


class ApiKeySummaryResponse(BaseModel):
    id: str
    name: str | None
    key_prefix: str | None
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    is_expired: bool


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)

    @field_validator("name")
    @classmethod
    def _strip_and_validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class CreateApiKeyResponse(BaseModel):
    api_key: str
    key: ApiKeySummaryResponse


def _serialize_user_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _coerce_non_empty_user_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_github_bearer_token(value: object) -> str | None:
    token = _coerce_non_empty_user_text(value)
    if token is None or _has_ascii_control(token) or _has_whitespace(token):
        return None
    return token


def _coerce_user_avatar_url(value: object) -> str | None:
    url = _coerce_non_empty_user_text(value)
    if url is None or _has_ascii_control(url):
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in _USER_ALLOWED_AVATAR_SCHEMES:
        return None
    if port is not None and not (1 <= port <= 65535):
        return None
    if not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    if _USER_URL_SECRET_PARAM_RE.search(parsed.query):
        return None
    if _USER_URL_SECRET_PARAM_RE.search(parsed.fragment):
        return None
    return url


def _coerce_user_int(value: object, fallback: int = 0) -> int:
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


def _coerce_user_float(value: object) -> float | None:
    normalized: float
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        normalized = float(value)
    elif isinstance(value, str):
        try:
            normalized = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return normalized if math.isfinite(normalized) else None


def _coerce_user_bool(value: object, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        normalized = float(value)
        return bool(normalized) if math.isfinite(normalized) else fallback
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"true", "1", "yes", "y", "on"}:
            return True
        if value in {"false", "0", "no", "n", "off", ""}:
            return False
    return fallback


def _verify_user_password(plain: object, hashed: object) -> bool:
    if not isinstance(plain, str):
        return False
    password_hash = _coerce_non_empty_user_text(hashed)
    if password_hash is None:
        return False
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except ValueError:
        return False


def _has_github_connection(user: object) -> bool:
    return bool(
        _coerce_non_empty_user_text(getattr(user, "github_id", None))
        or _coerce_github_bearer_token(getattr(user, "github_token", None))
    )


def _coerce_user_row_list(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _api_key_to_response(api_key: ApiKey) -> ApiKeySummaryResponse:
    return ApiKeySummaryResponse(
        id=str(getattr(api_key, "id", "")),
        name=_coerce_non_empty_user_text(getattr(api_key, "name", None)),
        key_prefix=_coerce_non_empty_user_text(getattr(api_key, "key_prefix", None)),
        created_at=(
            _serialize_user_timestamp(getattr(api_key, "created_at", None)) or ""
        ),
        last_used_at=_serialize_user_timestamp(getattr(api_key, "last_used", None)),
        expires_at=_serialize_user_timestamp(getattr(api_key, "expires_at", None)),
        is_expired=_coerce_user_bool(getattr(api_key, "is_expired", False), False),
    )


def _plan_display_name_for_user(plan: str, raw_display_name: object) -> str:
    plan_display_name = _coerce_non_empty_user_text(raw_display_name)
    if plan_display_name:
        return plan_display_name

    configured_name = PLANS.get(plan, {}).get("name")
    if isinstance(configured_name, str) and configured_name.strip():
        return configured_name.strip()

    if plan == "enterprise":
        return "Enterprise"

    return plan.capitalize()


def _user_to_profile_response(user: User) -> UserProfileResponse:
    plan = (_coerce_non_empty_user_text(getattr(user, "plan", None)) or "free").lower()
    plan_display_name = _plan_display_name_for_user(
        plan,
        getattr(user, "plan_display_name", None),
    )
    plan_status = (
        _coerce_non_empty_user_text(getattr(user, "plan_status", None)) or "active"
    )
    credits_remaining = _coerce_user_int(getattr(user, "credits_remaining", None), 0)
    topup_credits = _coerce_user_int(getattr(user, "topup_credits", None), 0)
    total_credits = _coerce_user_int(
        getattr(user, "total_credits", None),
        credits_remaining + topup_credits,
    )
    credits_used_this_month = _coerce_user_int(
        getattr(user, "credits_used_this_month", None),
        0,
    )

    return UserProfileResponse(
        id=str(getattr(user, "id", "")),
        email=_coerce_non_empty_user_text(getattr(user, "email", None)) or "",
        name=_coerce_non_empty_user_text(getattr(user, "name", None)),
        avatar_url=_coerce_user_avatar_url(getattr(user, "avatar_url", None)),
        github_connected=_has_github_connection(user),
        plan=plan,
        plan_display_name=plan_display_name,
        plan_status=plan_status,
        credits_remaining=credits_remaining,
        topup_credits=topup_credits,
        total_credits=total_credits,
        credits_used_this_month=credits_used_this_month,
        monthly_allocation=PLAN_CREDITS.get(plan, 0),
        subscription_period_end=_serialize_user_timestamp(
            getattr(user, "subscription_period_end", None)
        ),
        created_at=_serialize_user_timestamp(getattr(user, "created_at", None)) or "",
        last_active=_serialize_user_timestamp(getattr(user, "last_active", None)),
    )


def _session_to_summary(session: CodingSession) -> SessionSummary:
    mode = _coerce_non_empty_user_text(getattr(session, "mode", None)) or "unknown"
    status = _coerce_non_empty_user_text(getattr(session, "status", None)) or "unknown"
    return SessionSummary(
        id=str(getattr(session, "id", "")),
        mode=mode,
        prompt=_coerce_non_empty_user_text(getattr(session, "prompt", None)),
        repo_connected=_coerce_non_empty_user_text(
            getattr(session, "repo_connected", None)
        ),
        status=status,
        credits_charged=_coerce_user_int(getattr(session, "credits_charged", None), 0),
        lines_generated=_coerce_user_int(getattr(session, "lines_generated", None), 0),
        files_modified=_coerce_user_int(getattr(session, "files_modified", None), 0),
        nfet_phase_before=_coerce_non_empty_user_text(
            getattr(session, "nfet_phase_before", None)
        ),
        nfet_phase_after=_coerce_non_empty_user_text(
            getattr(session, "nfet_phase_after", None)
        ),
        es_score_before=_coerce_user_float(getattr(session, "es_score_before", None)),
        es_score_after=_coerce_user_float(getattr(session, "es_score_after", None)),
        output_summary=_coerce_non_empty_user_text(
            getattr(session, "output_summary", None)
        ),
        error_message=_coerce_non_empty_user_text(
            getattr(session, "error_message", None)
        ),
        started_at=(
            _serialize_user_timestamp(getattr(session, "started_at", None)) or ""
        ),
        completed_at=_serialize_user_timestamp(getattr(session, "completed_at", None)),
    )


def _balance_to_response(balance: object) -> CreditBalanceResponse:
    payload = balance if isinstance(balance, dict) else {}
    plan = _coerce_non_empty_user_text(payload.get("plan")) or "free"
    return CreditBalanceResponse(
        subscription_credits=_coerce_user_int(payload.get("subscription_credits"), 0),
        topup_credits=_coerce_user_int(payload.get("topup_credits"), 0),
        total=_coerce_user_int(payload.get("total"), 0),
        used_this_month=_coerce_user_int(payload.get("used_this_month"), 0),
        plan=plan,
        monthly_allocation=_coerce_user_int(
            payload.get("monthly_allocation"),
            PLAN_CREDITS.get(plan, 0),
        ),
    )


async def _fetch_github_user_from_token(token: str) -> dict[str, str | None]:
    token = _coerce_github_bearer_token(token)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "GitHub token was rejected. Use a token with repo, "
                "read:user, and user:email access."
            ),
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    try:
        async with httpx.AsyncClient(timeout=_GITHUB_API_TIMEOUT) as client:
            user_resp = await client.get(_GITHUB_USER_URL, headers=headers)
            if user_resp.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "GitHub token was rejected. Use a token with repo, "
                        "read:user, and user:email access."
                    ),
                )
            user_resp.raise_for_status()
            user_data = user_resp.json()
            if not isinstance(user_data, dict):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub validation failed. Try again.",
                )

            email = _coerce_non_empty_user_text(user_data.get("email"))
            if not email:
                emails_resp = await client.get(_GITHUB_EMAILS_URL, headers=headers)
                if emails_resp.status_code in {401, 403}:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            "GitHub token is missing user email access. Add "
                            "user:email and try again."
                        ),
                    )
                emails_resp.raise_for_status()
                emails = emails_resp.json()
                if not isinstance(emails, list):
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="GitHub validation failed. Try again.",
                    )
                primary = next(
                    (
                        entry
                        for entry in emails
                        if isinstance(entry, dict)
                        and _coerce_user_bool(entry.get("primary"))
                        and _coerce_user_bool(entry.get("verified"))
                    ),
                    None,
                )
                if primary:
                    email = _coerce_non_empty_user_text(primary.get("email"))
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="GitHub validation timed out. Try again.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub validation failed. Try again.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub validation failed. Try again.",
        ) from exc

    user_id_value = user_data.get("id")
    if not isinstance(user_id_value, (str, int)) or isinstance(user_id_value, bool):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub validation failed. Try again.",
        )
    user_id = _coerce_non_empty_user_text(str(user_id_value))
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub validation failed. Try again.",
        )

    name = _coerce_non_empty_user_text(user_data.get("name"))
    if not name:
        name = _coerce_non_empty_user_text(user_data.get("login"))

    avatar_url = _coerce_user_avatar_url(user_data.get("avatar_url"))

    return {
        "id": user_id,
        "name": name,
        "avatar_url": avatar_url,
        "email": email,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserProfileResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserProfileResponse:
    return _user_to_profile_response(current_user)


@router.patch("/me", response_model=UserProfileResponse)
async def update_me(
    body: UpdateUserRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    if body.name is not None:
        current_user.name = body.name
    if body.avatar_url is not None:
        current_user.avatar_url = body.avatar_url
    current_user.last_active = datetime.utcnow()
    await db.flush()

    return _user_to_profile_response(current_user)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    password_hash = _coerce_non_empty_user_text(
        getattr(current_user, "password_hash", None)
    )
    if password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password sign-in is not enabled for this account.",
        )

    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters.",
        )

    if not _verify_user_password(body.current_password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    current_user.password_hash = bcrypt.hashpw(
        body.new_password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")
    current_user.last_active = datetime.utcnow()
    await db.flush()


@router.delete("/me/github", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_github_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    current_user.github_id = None
    current_user.github_token = None
    current_user.last_active = datetime.utcnow()
    await db.flush()


@router.post("/me/github/token", response_model=UserProfileResponse)
async def connect_github_token(
    body: ConnectGitHubTokenRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    token = body.token.strip()
    github_user = await _fetch_github_user_from_token(token)

    current_user.github_id = github_user["id"]
    current_user.github_token = token
    if github_user.get("name") and not current_user.name:
        current_user.name = github_user["name"]
    if github_user.get("avatar_url") and not current_user.avatar_url:
        current_user.avatar_url = github_user["avatar_url"]
    current_user.last_active = datetime.utcnow()
    await db.flush()

    return _user_to_profile_response(current_user)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    body: DeleteUserRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if body.confirm != "DELETE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='You must send {"confirm": "DELETE"} to delete your account.',
        )

    # Cancel any active Stripe subscription
    if current_user.subscription_id:
        billing = BillingService(db)
        try:
            await billing.cancel_subscription(current_user.id)
        except BillingError:
            pass  # Best effort — proceed with deletion regardless

    user_id = current_user.id
    session_ids = select(CodingSession.id).where(CodingSession.user_id == user_id)
    project_ids = select(Project.id).where(Project.user_id == user_id)
    build_project_ids = select(BuildProject.id).where(BuildProject.user_id == user_id)

    # Remove dependent rows explicitly because the current schema does not
    # provide ON DELETE CASCADE for user-owned data.
    await db.execute(
        delete(BuildCheckpoint).where(BuildCheckpoint.project_id.in_(build_project_ids))
    )
    await db.execute(
        delete(BuildFile).where(BuildFile.project_id.in_(build_project_ids))
    )
    await db.execute(delete(BuildProject).where(BuildProject.user_id == user_id))

    await db.execute(
        delete(ProjectVersion).where(
            or_(
                ProjectVersion.project_id.in_(project_ids),
                ProjectVersion.session_id.in_(session_ids),
            )
        )
    )
    await db.execute(
        delete(Export).where(
            or_(
                Export.user_id == user_id,
                Export.project_id.in_(project_ids),
            )
        )
    )
    await db.execute(delete(Project).where(Project.user_id == user_id))

    await db.execute(delete(SessionCost).where(SessionCost.user_id == user_id))
    await db.execute(delete(MemoryUpdateLog).where(MemoryUpdateLog.user_id == user_id))
    await db.execute(delete(UserMemory).where(UserMemory.user_id == user_id))
    await db.execute(delete(ApiKey).where(ApiKey.user_id == user_id))
    await db.execute(
        delete(CreditTransaction).where(CreditTransaction.user_id == user_id)
    )
    await db.execute(delete(Repository).where(Repository.user_id == user_id))
    await db.execute(
        delete(Referral).where(
            or_(Referral.referrer_id == user_id, Referral.referred_id == user_id)
        )
    )
    await db.execute(delete(CodingSession).where(CodingSession.user_id == user_id))
    await db.execute(
        delete(SecurityAuditLog).where(SecurityAuditLog.user_id == user_id)
    )
    await db.execute(delete(User).where(User.id == user_id))
    await db.flush()


@router.get("/me/credits", response_model=CreditBalanceResponse)
async def get_my_credits(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreditBalanceResponse:
    credit_service = CreditService(db)
    balance = await credit_service.get_balance(current_user.id)
    return _balance_to_response(balance)


@router.get("/me/sessions", response_model=PaginatedSessionsResponse)
async def get_my_sessions(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedSessionsResponse:
    if not isinstance(limit, int):
        default_limit = getattr(limit, "default", 20)
        limit = default_limit if isinstance(default_limit, int) else 20
    if not isinstance(offset, int):
        default_offset = getattr(offset, "default", 0)
        offset = default_offset if isinstance(default_offset, int) else 0
    if not isinstance(status, (str, type(None))):
        default_status = getattr(status, "default", None)
        status = default_status if isinstance(default_status, str) else None

    # Get total count
    count_stmt = select(func.count()).select_from(CodingSession).where(
        CodingSession.user_id == current_user.id
    )
    if status:
        count_stmt = count_stmt.where(CodingSession.status == status)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    # Get paginated sessions
    stmt = (
        select(CodingSession)
        .where(CodingSession.user_id == current_user.id)
        .order_by(CodingSession.started_at.desc())
    )
    if status:
        stmt = stmt.where(CodingSession.status == status)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    sessions = _coerce_user_row_list(result.scalars().all())

    return PaginatedSessionsResponse(
        sessions=[_session_to_summary(s) for s in sessions],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/me/api-keys", response_model=list[ApiKeySummaryResponse])
async def list_api_keys(
    current_user: User = Depends(require_plan("pro")),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeySummaryResponse]:
    stmt = (
        select(ApiKey)
        .where(ApiKey.user_id == current_user.id)
        .order_by(ApiKey.created_at.desc())
    )
    result = await db.execute(stmt)
    api_keys = _coerce_user_row_list(result.scalars().all())
    return [_api_key_to_response(api_key) for api_key in api_keys]


@router.post(
    "/me/api-keys",
    response_model=CreateApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    body: CreateApiKeyRequest,
    current_user: User = Depends(require_plan("pro")),
    db: AsyncSession = Depends(get_db),
) -> CreateApiKeyResponse:
    plaintext, hashed = generate_api_key()
    expires_at = None
    if body.expires_in_days is not None:
        expires_at = datetime.utcnow() + timedelta(days=body.expires_in_days)

    api_key = ApiKey(
        user_id=current_user.id,
        name=body.name.strip(),
        key_hash=hashed,
        key_prefix=plaintext[:12],
        expires_at=expires_at,
    )
    db.add(api_key)
    current_user.last_active = datetime.utcnow()
    await db.flush()

    audit = AuditLogger(db)
    await audit.log(
        user_id=current_user.id,
        action=ACTION_API_KEY_CREATE,
        resource_type="api_key",
        resource_id=api_key.id,
        result="success",
        metadata={"name": api_key.name, "key_prefix": api_key.key_prefix},
    )

    return CreateApiKeyResponse(
        api_key=plaintext,
        key=_api_key_to_response(api_key),
    )


@router.delete("/me/api-keys/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    api_key_id: str,
    current_user: User = Depends(require_plan("pro")),
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        key_uuid = uuid.UUID(api_key_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid API key ID format",
        )

    stmt = select(ApiKey).where(
        ApiKey.id == key_uuid,
        ApiKey.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    audit = AuditLogger(db)
    await audit.log(
        user_id=current_user.id,
        action=ACTION_API_KEY_DELETE,
        resource_type="api_key",
        resource_id=api_key.id,
        result="success",
        metadata={"name": api_key.name, "key_prefix": api_key.key_prefix},
    )
    await db.delete(api_key)
    current_user.last_active = datetime.utcnow()
    await db.flush()
