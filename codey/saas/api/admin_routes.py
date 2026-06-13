"""Admin dashboard API — stats, user management, announcements."""

from __future__ import annotations

import math
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Float, Integer, String, case, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.billing.plans import PLANS
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

    plan = _coerce_non_empty_admin_text(getattr(current_user, "plan", None))
    if (plan or "").lower() != "enterprise":
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
    plan: float(details.get("price_monthly", 0)) / 100.0
    for plan, details in PLANS.items()
}
_PLAN_MONTHLY_USD.setdefault("enterprise", 199.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_admin_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _has_ascii_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _coerce_non_empty_admin_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if _has_ascii_control(value):
        return None
    return value or None


def _coerce_admin_int(value: object, fallback: int = 0) -> int:
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


def _coerce_admin_float(value: object, fallback: float = 0.0) -> float:
    normalized: float
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        normalized = float(value)
    elif isinstance(value, str):
        try:
            normalized = float(value.strip())
        except ValueError:
            return fallback
    else:
        return fallback
    return normalized if math.isfinite(normalized) else fallback


def _coerce_admin_row_list(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _user_to_search_result(user: User) -> UserSearchResult:
    return UserSearchResult(
        id=str(getattr(user, "id", "")),
        email=_coerce_non_empty_admin_text(getattr(user, "email", None)) or "",
        name=_coerce_non_empty_admin_text(getattr(user, "name", None)),
        plan=(
            _coerce_non_empty_admin_text(getattr(user, "plan", None)) or "free"
        ).lower(),
        credits_remaining=_coerce_admin_int(
            getattr(user, "credits_remaining", None),
            0,
        ),
        topup_credits=_coerce_admin_int(getattr(user, "topup_credits", None), 0),
        created_at=_serialize_admin_timestamp(getattr(user, "created_at", None)) or "",
        last_active=_serialize_admin_timestamp(getattr(user, "last_active", None)),
    )


def _credit_adjustment_to_response(
    user_id: object,
    user: User,
    amount: object,
    reason: object,
) -> CreditAdjustmentResponse:
    return CreditAdjustmentResponse(
        user_id=str(user_id),
        new_credits_remaining=_coerce_admin_int(
            getattr(user, "credits_remaining", None),
            0,
        ),
        new_topup_credits=_coerce_admin_int(getattr(user, "topup_credits", None), 0),
        adjustment=_coerce_admin_int(amount, 0),
        reason=_coerce_non_empty_admin_text(reason) or "",
    )


def _announcement_to_response(payload: object) -> AnnouncementResponse:
    data = payload if isinstance(payload, dict) else {}
    level = _coerce_non_empty_admin_text(data.get("level")) or "info"
    if level not in {"info", "warning", "error"}:
        level = "info"
    return AnnouncementResponse(
        message=_coerce_non_empty_admin_text(data.get("message")),
        level=level,
    )


def _plan_breakdown_to_response(row: object) -> PlanBreakdown:
    try:
        plan_raw, count_raw = row[0], row[1]
    except (TypeError, IndexError, KeyError):
        plan_raw, count_raw = None, 0
    return PlanBreakdown(
        plan=(_coerce_non_empty_admin_text(plan_raw) or "free").lower(),
        count=_coerce_admin_int(count_raw, 0),
    )


def _stats_to_response(
    *,
    total_users: object,
    users_by_plan: list[object],
    total_credits_used: object,
    total_api_cost: object,
    total_sessions: object,
    signups_last_30: object,
    paid_users: object,
) -> AdminStatsResponse:
    total_users_count = _coerce_admin_int(total_users, 0)
    plan_breakdown = [_plan_breakdown_to_response(row) for row in users_by_plan]
    mrr = sum(
        _PLAN_MONTHLY_USD.get(plan.plan, 0.0) * plan.count
        for plan in plan_breakdown
    )
    total_api_cost_usd = _coerce_admin_float(total_api_cost, 0.0)
    gross_margin = ((mrr - total_api_cost_usd) / mrr * 100) if mrr > 0 else 0.0
    paid_users_count = _coerce_admin_int(paid_users, 0)
    conversion_rate = (
        paid_users_count / total_users_count * 100
        if total_users_count > 0
        else 0.0
    )

    return AdminStatsResponse(
        total_users=total_users_count,
        users_by_plan=plan_breakdown,
        mrr_usd=round(mrr, 2),
        total_credits_used=_coerce_admin_int(total_credits_used, 0),
        total_api_cost_usd=round(total_api_cost_usd, 2),
        gross_margin=round(gross_margin, 2),
        total_sessions=_coerce_admin_int(total_sessions, 0),
        signups_last_30_days=_coerce_admin_int(signups_last_30, 0),
        conversion_rate=round(conversion_rate, 2),
    )


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
    users_by_plan = _coerce_admin_row_list(plan_result.all())

    # Total credits used this month across all users
    credits_result = await db.execute(
        select(func.coalesce(func.sum(User.credits_used_this_month), 0))
    )
    total_credits_used = credits_result.scalar_one()

    # Total API cost from session_costs
    cost_result = await db.execute(
        select(func.coalesce(func.sum(SessionCost.api_cost_usd), 0.0))
    )
    total_api_cost = cost_result.scalar_one()

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

    return _stats_to_response(
        total_users=total_users,
        users_by_plan=users_by_plan,
        total_credits_used=total_credits_used,
        total_api_cost=total_api_cost,
        total_sessions=total_sessions,
        signups_last_30=signups_last_30,
        paid_users=paid_users,
    )


@router.get("/users", response_model=list[UserSearchResult])
async def search_users(
    search: str = Query(..., min_length=1, description="Email substring to search"),
    limit: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UserSearchResult]:
    """Search users by email substring."""
    if not isinstance(limit, int):
        default_limit = getattr(limit, "default", 50)
        limit = default_limit if isinstance(default_limit, int) else 50

    result = await db.execute(
        select(User)
        .where(User.email.ilike(f"%{search}%"))
        .order_by(User.created_at.desc())
        .limit(limit)
    )
    users = _coerce_admin_row_list(result.scalars().all())
    return [_user_to_search_result(u) for u in users]


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
    old_topup = _coerce_admin_int(getattr(user, "topup_credits", None), 0)
    new_topup = max(0, old_topup + body.amount)
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

    return _credit_adjustment_to_response(user_id, user, body.amount, body.reason)


@router.post("/announcement", response_model=AnnouncementResponse)
async def set_announcement(
    body: AnnouncementRequest,
    _admin: User = Depends(require_admin),
) -> AnnouncementResponse:
    """Set or clear a site-wide banner announcement.

    Pass ``message: null`` to clear the current announcement.
    """
    _announcement["message"] = body.message
    _announcement["level"] = (
        body.level if body.level in ("info", "warning", "error") else "info"
    )
    return _announcement_to_response(_announcement)


@router.get("/announcement", response_model=AnnouncementResponse)
async def get_announcement() -> AnnouncementResponse:
    """Get the current site-wide announcement (public endpoint)."""
    return _announcement_to_response(_announcement)
