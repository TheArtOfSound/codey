from __future__ import annotations

import math
from typing import Callable
import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.billing.plans import PLANS
from codey.saas.auth.cookies import SESSION_COOKIE_NAME
from codey.saas.auth.jwt import decode_access_token, normalize_access_token_candidate
from codey.saas.database import get_db, set_db_user_context
from codey.saas.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def _coerce_plan_price(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        try:
            parsed = float(value)
        except OverflowError:
            return 0.0
    elif isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return 0.0
    else:
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _normalize_plan_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value or None


def _plan_display_name(value: object, fallback_plan: str | None) -> str:
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return (fallback_plan or "free").capitalize()


def _coerce_user_context_id(value: object) -> str | None:
    if isinstance(value, uuid.UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


# Ordered from lowest to highest privilege using the billing configuration.
PLAN_LEVELS: dict[str, int] = {
    plan: index
    for index, (plan, _details) in enumerate(
        sorted(
            PLANS.items(),
            key=lambda item: (_coerce_plan_price(item[1].get("price_monthly")), item[0]),
        )
    )
}
PLAN_LEVELS.setdefault("enterprise", max(PLAN_LEVELS.values(), default=-1) + 1)


async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract user from a Bearer JWT and return the corresponding database row.

    Raises ``HTTPException(401)`` if the token is invalid or the user does not
    exist.
    """
    token = normalize_access_token_candidate(token) or normalize_access_token_candidate(
        request.cookies.get(SESSION_COOKIE_NAME)
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    user_id = normalize_access_token_candidate(payload.get("sub"))

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    context_user_id = _coerce_user_context_id(getattr(user, "id", None))
    if context_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await set_db_user_context(db, context_user_id)
    return user


def require_plan(minimum: str) -> Callable:
    """Return a FastAPI dependency that enforces a minimum plan level.

    Usage::

        @router.get("/pro-feature")
        async def pro_feature(user: User = Depends(require_plan("pro"))):
            ...
    """
    minimum_plan = _normalize_plan_name(minimum) or "free"
    if minimum_plan not in PLAN_LEVELS:
        raise ValueError(f"Unknown subscription plan: {minimum_plan}")
    minimum_level = PLAN_LEVELS[minimum_plan]
    minimum_display_name = PLANS.get(minimum_plan, {}).get("name")
    if not isinstance(minimum_display_name, str) or not minimum_display_name.strip():
        minimum_display_name = minimum_plan.capitalize()

    async def _check_plan(
        current_user: User = Depends(get_current_user),
    ) -> User:
        user_plan = _normalize_plan_name(getattr(current_user, "plan", None))
        user_level = PLAN_LEVELS.get(user_plan or "", 0)
        if user_level < minimum_level:
            user_display_name = _plan_display_name(
                getattr(current_user, "plan_display_name", None),
                user_plan,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This feature requires the {minimum_display_name} plan or above. "
                    f"Your current plan is {user_display_name}."
                ),
            )
        return current_user

    return _check_plan
