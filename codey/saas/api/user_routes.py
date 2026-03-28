from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.billing.service import BillingService
from codey.saas.credits.service import CreditService, PLAN_CREDITS
from codey.saas.database import get_db
from codey.saas.models import CodingSession, User

router = APIRouter(prefix="/users", tags=["users"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UserProfileResponse(BaseModel):
    id: str
    email: str
    name: str | None
    avatar_url: str | None
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


class DeleteUserRequest(BaseModel):
    confirm: str


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
    status: str
    credits_charged: int
    lines_generated: int
    files_modified: int
    started_at: str
    completed_at: str | None


class PaginatedSessionsResponse(BaseModel):
    sessions: list[SessionSummary]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserProfileResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserProfileResponse:
    return UserProfileResponse(
        id=str(current_user.id),
        email=current_user.email,
        name=current_user.name,
        avatar_url=current_user.avatar_url,
        plan=current_user.plan,
        plan_display_name=current_user.plan_display_name,
        plan_status=current_user.plan_status,
        credits_remaining=current_user.credits_remaining,
        topup_credits=current_user.topup_credits,
        total_credits=current_user.total_credits,
        credits_used_this_month=current_user.credits_used_this_month,
        monthly_allocation=PLAN_CREDITS.get(current_user.plan, 0),
        subscription_period_end=(
            current_user.subscription_period_end.isoformat()
            if current_user.subscription_period_end
            else None
        ),
        created_at=current_user.created_at.isoformat(),
        last_active=(
            current_user.last_active.isoformat() if current_user.last_active else None
        ),
    )


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

    return UserProfileResponse(
        id=str(current_user.id),
        email=current_user.email,
        name=current_user.name,
        avatar_url=current_user.avatar_url,
        plan=current_user.plan,
        plan_display_name=current_user.plan_display_name,
        plan_status=current_user.plan_status,
        credits_remaining=current_user.credits_remaining,
        topup_credits=current_user.topup_credits,
        total_credits=current_user.total_credits,
        credits_used_this_month=current_user.credits_used_this_month,
        monthly_allocation=PLAN_CREDITS.get(current_user.plan, 0),
        subscription_period_end=(
            current_user.subscription_period_end.isoformat()
            if current_user.subscription_period_end
            else None
        ),
        created_at=current_user.created_at.isoformat(),
        last_active=(
            current_user.last_active.isoformat() if current_user.last_active else None
        ),
    )


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
        except Exception:
            pass  # Best effort — proceed with deletion regardless

    await db.delete(current_user)
    await db.flush()


@router.get("/me/credits", response_model=CreditBalanceResponse)
async def get_my_credits(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreditBalanceResponse:
    credit_service = CreditService(db)
    balance = await credit_service.get_balance(current_user.id)
    return CreditBalanceResponse(**balance)


@router.get("/me/sessions", response_model=PaginatedSessionsResponse)
async def get_my_sessions(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedSessionsResponse:
    # Get total count
    count_stmt = (
        select(func.count())
        .select_from(CodingSession)
        .where(CodingSession.user_id == current_user.id)
    )
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    # Get paginated sessions
    stmt = (
        select(CodingSession)
        .where(CodingSession.user_id == current_user.id)
        .order_by(CodingSession.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    sessions = result.scalars().all()

    return PaginatedSessionsResponse(
        sessions=[
            SessionSummary(
                id=str(s.id),
                mode=s.mode,
                prompt=s.prompt,
                status=s.status,
                credits_charged=s.credits_charged,
                lines_generated=s.lines_generated,
                files_modified=s.files_modified,
                started_at=s.started_at.isoformat(),
                completed_at=s.completed_at.isoformat() if s.completed_at else None,
            )
            for s in sessions
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
