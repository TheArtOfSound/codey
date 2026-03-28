from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.oauth import oauth_github_url, oauth_google_url
from codey.saas.auth.service import AuthService
from codey.saas.database import get_db
from codey.saas.security.audit import AuditLogger, ACTION_LOGIN_SUCCESS, ACTION_LOGIN_FAILURE

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ResetPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordConfirmRequest(BaseModel):
    token: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None
    avatar_url: str | None
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


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_to_response(user) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        avatar_url=user.avatar_url,
        plan=user.plan,
        plan_status=user.plan_status,
        credits_remaining=user.credits_remaining,
        topup_credits=user.topup_credits,
        total_credits=user.total_credits,
        created_at=user.created_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, db: AsyncSession = Depends(get_db)) -> AuthResponse:
    auth_service = AuthService(db)
    user, token = await auth_service.signup(
        email=body.email,
        password=body.password,
        name=body.name,
    )
    return AuthResponse(user=_user_to_response(user), token=token)


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> AuthResponse:
    auth_service = AuthService(db)
    audit = AuditLogger(db)
    try:
        user, token = await auth_service.login(email=body.email, password=body.password)
        await audit.log(user_id=user.id, action=ACTION_LOGIN_SUCCESS, result="success")
        return AuthResponse(user=_user_to_response(user), token=token)
    except HTTPException:
        await audit.log(user_id=None, action=ACTION_LOGIN_FAILURE, result="failure", failure_reason=f"Invalid credentials for {body.email}")
        raise


@router.get("/github", response_model=OAuthUrlResponse)
async def github_redirect() -> OAuthUrlResponse:
    return OAuthUrlResponse(url=oauth_github_url())


@router.get("/github/callback", response_model=AuthResponse)
async def github_callback(code: str, db: AsyncSession = Depends(get_db)) -> AuthResponse:
    auth_service = AuthService(db)
    user, token = await auth_service.github_callback(code)
    return AuthResponse(user=_user_to_response(user), token=token)


@router.get("/google", response_model=OAuthUrlResponse)
async def google_redirect() -> OAuthUrlResponse:
    return OAuthUrlResponse(url=oauth_google_url())


@router.get("/google/callback", response_model=AuthResponse)
async def google_callback(code: str, db: AsyncSession = Depends(get_db)) -> AuthResponse:
    auth_service = AuthService(db)
    user, token = await auth_service.google_callback(code)
    return AuthResponse(user=_user_to_response(user), token=token)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    auth_service = AuthService(db)
    _token = await auth_service.request_password_reset(body.email)
    # In production, send the token via email here.
    # Always return the same message to prevent user enumeration.
    return MessageResponse(message="If an account with that email exists, a reset link has been sent.")


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
