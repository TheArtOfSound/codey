from __future__ import annotations

import logging
import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.cookies import SESSION_COOKIE_NAME
from codey.saas.database import get_db, set_db_user_context

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


def _request_user_id(request: Request) -> uuid.UUID:
    try:
        from codey.saas.auth.jwt import decode_access_token, normalize_access_token_candidate
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        ) from exc

    auth_header = request.headers.get("authorization", "")
    candidates: list[object] = []
    if isinstance(auth_header, str):
        auth_parts = auth_header.strip().split(None, 1)
        if len(auth_parts) == 2 and auth_parts[0].lower() == "bearer":
            candidates.append(auth_parts[1])
    candidates.append(request.cookies.get(SESSION_COOKIE_NAME))

    for candidate in candidates:
        normalized = normalize_access_token_candidate(candidate)
        if normalized is None:
            continue
        try:
            payload = decode_access_token(normalized)
        except Exception:
            continue
        user_id_str = normalize_access_token_candidate(payload.get("sub"))
        if user_id_str is None:
            continue
        try:
            return uuid.UUID(user_id_str)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            ) from None

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


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
        user_id = _request_user_id(request)
        await set_db_user_context(db, str(user_id))

        # Extract resource_id from path parameters.
        resource_id_str = request.path_params.get("resource_id")
        if resource_id_str is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing resource_id path parameter",
            )
        try:
            resource_id = uuid.UUID(str(resource_id_str))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid resource_id path parameter",
            ) from exc

        return await verify_ownership(user_id, resource_id, resource_type, db)

    return _dependency
