"""Admin dashboard API — stats, user management, announcements."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Float, Integer, String, case, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.database import get_db
from codey.saas.models.coding_session import CodingSession
from codey.saas.models.cost_tracking import SessionCost
from codey.saas.models.credit_transaction import CreditTransaction
from codey.saas.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# In-memory store for site-wide announcement (swap to Redis in production)
# ---------------------------------------------------------------------------

_announcement: dict[str, str | None] = {"message": None, "level": "info"}


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that ensures the caller has admin privileges.

    Admin is determined by the user's plan being 'enterprise' or by an
    explicit ``is_admin`` flag when one is added to the User model.
    For now, enterprise plan holders have admin access.
    """
    # Check for explicit admin attribute first (forward-compatible)
    is_admin = getattr(current_user, "is_admin", None)
    if is_admin is True:
        return current_user

    if current_user.plan != "enterprise":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlanBreakdown(BaseModel):
    plan: str
    count: int


class AdminStatsResponse(BaseModel):
    total_users: int
    users_by_plan: list[PlanBreakdown]
    mrr_usd: float
    total_credits_used: int
    total_api_cost_usd: float
    gross_margin: float
    total_sessions: int
    signups_last_30_days: int
    conversion_rate: float


class UserSearchResult(BaseModel):
    id: str
    email: str
    name: str | None
    plan: str
    credits_remaining: int
    topup_credits: int
    created_at: str
    last_active: str | None


class CreditAdjustmentRequest(BaseModel):
    amount: int
    reason: str


class CreditAdjustmentResponse(BaseModel):
    user_id: str
    new_credits_remaining: int
    new_topup_credits: int
    adjustment: int
    reason: str


class AnnouncementRequest(BaseModel):
    message: str | None
    level: str = "info"


class AnnouncementResponse(BaseModel):
    message: str | None
    level: str


# ---------------------------------------------------------------------------
# Plan pricing for MRR calculation
# ---------------------------------------------------------------------------

_PLAN_MONTHLY_USD: dict[str, float] = {
    "free": 0.0,
    "starter": 9.0,
    "pro": 29.0,
    "team": 79.0,
    "enterprise": 199.0,
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=AdminStatsResponse)
async def get_admin_stats(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminStatsResponse:
    """Aggregate platform statistics for the admin dashboard."""

    # Total users
    total_result = await db.execute(select(func.count(User.id)))
    total_users = total_result.scalar_one()

    # Users by plan
    plan_result = await db.execute(
        select(User.plan, func.count(User.id))
        .group_by(User.plan)
        .order_by(func.count(User.id).desc())
    )
    users_by_plan = [
        PlanBreakdown(plan=row[0], count=row[1]) for row in plan_result.all()
    ]

    # MRR — sum of active paying users' plan costs
    mrr = sum(
        _PLAN_MONTHLY_USD.get(pb.plan, 0.0) * pb.count for pb in users_by_plan
    )

    # Total credits used this month across all users
    credits_result = await db.execute(
        select(func.coalesce(func.sum(User.credits_used_this_month), 0))
    )
    total_credits_used = credits_result.scalar_one()

    # Total API cost from session_costs
    cost_result = await db.execute(
        select(func.coalesce(func.sum(SessionCost.api_cost_usd), 0.0))
    )
    total_api_cost = float(cost_result.scalar_one())

    # Gross margin
    gross_margin = ((mrr - total_api_cost) / mrr * 100) if mrr > 0 else 0.0

    # Total sessions
    session_result = await db.execute(select(func.count(CodingSession.id)))
    total_sessions = session_result.scalar_one()

    # Signups last 30 days
    thirty_days_ago = datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    from datetime import timedelta
    thirty_days_ago -= timedelta(days=30)

    signup_result = await db.execute(
        select(func.count(User.id)).where(User.created_at >= thirty_days_ago)
    )
    signups_last_30 = signup_result.scalar_one()

    # Conversion rate: paid users / total users
    paid_result = await db.execute(
        select(func.count(User.id)).where(User.plan != "free")
    )
    paid_users = paid_result.scalar_one()
    conversion_rate = (paid_users / total_users * 100) if total_users > 0 else 0.0

    return AdminStatsResponse(
        total_users=total_users,
        users_by_plan=users_by_plan,
        mrr_usd=round(mrr, 2),
        total_credits_used=total_credits_used,
        total_api_cost_usd=round(total_api_cost, 2),
        gross_margin=round(gross_margin, 2),
        total_sessions=total_sessions,
        signups_last_30_days=signups_last_30,
        conversion_rate=round(conversion_rate, 2),
    )


@router.get("/users", response_model=list[UserSearchResult])
async def search_users(
    search: str = Query(..., min_length=1, description="Email substring to search"),
    limit: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UserSearchResult]:
    """Search users by email substring."""
    result = await db.execute(
        select(User)
        .where(User.email.ilike(f"%{search}%"))
        .order_by(User.created_at.desc())
        .limit(limit)
    )
    users = result.scalars().all()
    return [
        UserSearchResult(
            id=str(u.id),
            email=u.email,
            name=u.name,
            plan=u.plan,
            credits_remaining=u.credits_remaining,
            topup_credits=u.topup_credits,
            created_at=u.created_at.isoformat(),
            last_active=u.last_active.isoformat() if u.last_active else None,
        )
        for u in users
    ]


@router.post("/users/{user_id}/credits", response_model=CreditAdjustmentResponse)
async def adjust_credits(
    user_id: uuid.UUID,
    body: CreditAdjustmentRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CreditAdjustmentResponse:
    """Manually adjust a user's credits (positive to add, negative to remove)."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if body.amount == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Adjustment amount cannot be zero",
        )

    # Apply to topup_credits (admin adjustments are separate from plan credits)
    new_topup = max(0, user.topup_credits + body.amount)
    old_topup = user.topup_credits
    user.topup_credits = new_topup

    # Log the transaction
    tx = CreditTransaction(
        user_id=user_id,
        amount=body.amount,
        type="admin_adjustment",
        description=f"Admin adjustment: {body.reason}",
        credits_before=old_topup,
        credits_after=new_topup,
    )
    db.add(tx)
    await db.flush()

    return CreditAdjustmentResponse(
        user_id=str(user_id),
        new_credits_remaining=user.credits_remaining,
        new_topup_credits=user.topup_credits,
        adjustment=body.amount,
        reason=body.reason,
    )


@router.post("/announcement", response_model=AnnouncementResponse)
async def set_announcement(
    body: AnnouncementRequest,
    _admin: User = Depends(require_admin),
) -> AnnouncementResponse:
    """Set or clear a site-wide banner announcement.

    Pass ``message: null`` to clear the current announcement.
    """
    _announcement["message"] = body.message
    _announcement["level"] = body.level if body.level in ("info", "warning", "error") else "info"
    return AnnouncementResponse(
        message=_announcement["message"],
        level=_announcement["level"],
    )


@router.get("/announcement", response_model=AnnouncementResponse)
async def get_announcement() -> AnnouncementResponse:
    """Get the current site-wide announcement (public endpoint)."""
    return AnnouncementResponse(
        message=_announcement["message"],
        level=_announcement["level"],  # type: ignore[arg-type]
    )
