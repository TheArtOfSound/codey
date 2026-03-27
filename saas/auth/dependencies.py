from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.jwt import decode_access_token
from codey.saas.database import get_db
from codey.saas.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Ordered from lowest to highest privilege
PLAN_LEVELS: dict[str, int] = {
    "free": 0,
    "starter": 1,
    "pro": 2,
    "team": 3,
    "enterprise": 4,
}


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract user from a Bearer JWT and return the corresponding database row.

    Raises ``HTTPException(401)`` if the token is invalid or the user does not
    exist.
    """
    payload = decode_access_token(token)
    user_id: str | None = payload.get("sub")

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

    return user


def require_plan(minimum: str) -> Callable:
    """Return a FastAPI dependency that enforces a minimum plan level.

    Usage::

        @router.get("/pro-feature")
        async def pro_feature(user: User = Depends(require_plan("pro"))):
            ...
    """
    minimum_level = PLAN_LEVELS.get(minimum, 0)

    async def _check_plan(
        current_user: User = Depends(get_current_user),
    ) -> User:
        user_level = PLAN_LEVELS.get(current_user.plan, 0)
        if user_level < minimum_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This feature requires the {minimum.capitalize()} plan or above. "
                    f"Your current plan is {current_user.plan_display_name}."
                ),
            )
        return current_user

    return _check_plan
