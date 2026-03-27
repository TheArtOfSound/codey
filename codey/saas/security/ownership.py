from __future__ import annotations

import logging
import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.database import get_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resource-type -> (table_name, user_id_column) mapping.
# We use raw SQL via text() so we don't need to import every model and risk
# circular dependencies.  The table/column names are fixed schema constants.
# ---------------------------------------------------------------------------

_RESOURCE_MAP: dict[str, tuple[str, str]] = {
    "session": ("coding_sessions", "user_id"),
    "repository": ("repositories", "user_id"),
    "project": ("projects", "user_id"),
    "export": ("exports", "user_id"),
    "memory": ("user_memory", "user_id"),
}


async def verify_ownership(
    user_id: uuid.UUID,
    resource_id: uuid.UUID,
    resource_type: str,
    db: AsyncSession,
) -> bool:
    """Verify that *user_id* owns *resource_id* of the given *resource_type*.

    Returns ``True`` on success.
    Raises ``HTTPException(403)`` if the resource exists but belongs to another
    user, or ``HTTPException(404)`` if the resource does not exist at all.

    Failed ownership checks are logged to the ``security_audit_log`` table for
    forensic review.
    """
    mapping = _RESOURCE_MAP.get(resource_type)
    if mapping is None:
        raise ValueError(f"Unknown resource type: {resource_type!r}")

    table_name, user_col = mapping

    # Use text() to avoid importing models and to keep this module decoupled.
    from sqlalchemy import text

    row = (
        await db.execute(
            text(f"SELECT {user_col} FROM {table_name} WHERE id = :rid"),  # noqa: S608
            {"rid": resource_id},
        )
    ).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{resource_type.capitalize()} not found",
        )

    owner_id = row[0]
    if uuid.UUID(str(owner_id)) != user_id:
        # Log the failed access attempt.
        await _log_ownership_violation(db, user_id, resource_id, resource_type)
        logger.warning(
            "Ownership violation: user=%s attempted to access %s/%s owned by %s",
            user_id,
            resource_type,
            resource_id,
            owner_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this resource",
        )

    return True


async def _log_ownership_violation(
    db: AsyncSession,
    user_id: uuid.UUID,
    resource_id: uuid.UUID,
    resource_type: str,
) -> None:
    """Insert an ownership-violation audit record directly via SQL.

    We bypass the AuditLogger class to avoid circular imports while keeping
    the audit trail intact.
    """
    from sqlalchemy import text

    await db.execute(
        text(
            """
            INSERT INTO security_audit_log
                (id, user_id, action, resource_type, resource_id, result, created_at)
            VALUES
                (gen_random_uuid(), :uid, 'ownership_violation', :rtype, :rid, 'failure', now())
            """
        ),
        {"uid": user_id, "rtype": resource_type, "rid": resource_id},
    )
    await db.flush()


# ---------------------------------------------------------------------------
# FastAPI dependency factory
# ---------------------------------------------------------------------------


def require_ownership(resource_type: str):
    """Return a FastAPI dependency that verifies the current user owns the resource.

    The resource ID is extracted from the path parameter ``resource_id``.

    Usage::

        @router.get("/sessions/{resource_id}")
        async def get_session(
            resource_id: UUID,
            user: User = Depends(get_current_user),
            _owner: bool = Depends(require_ownership("session")),
        ):
            ...
    """

    async def _dependency(
        request: Request,
        db: AsyncSession = Depends(get_db),
    ) -> bool:
        # Extract user from request state (set by get_current_user).
        from codey.saas.auth.dependencies import get_current_user

        # We need to resolve the user manually since we can't Depends() on
        # get_current_user here without it running twice.  Instead we look
        # for the user in the path operation's resolved dependencies via
        # request.state, or re-resolve the token.
        token = request.headers.get("authorization", "")
        if not token.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        from codey.saas.auth.jwt import decode_access_token

        payload = decode_access_token(token.split(" ", 1)[1])
        user_id_str = payload.get("sub")
        if user_id_str is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        user_id = uuid.UUID(user_id_str)

        # Extract resource_id from path parameters.
        resource_id_str = request.path_params.get("resource_id")
        if resource_id_str is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing resource_id path parameter",
            )
        resource_id = uuid.UUID(str(resource_id_str))

        return await verify_ownership(user_id, resource_id, resource_type, db)

    return _dependency
