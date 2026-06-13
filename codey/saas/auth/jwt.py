from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any

try:
    from fastapi import HTTPException, status
except ModuleNotFoundError as exc:
    if exc.name != "fastapi":
        raise
    _FASTAPI_IMPORT_ERROR = exc

    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(
            self,
            status_code: int,
            detail: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail
            self.headers = headers

    class _StatusFallback:
        HTTP_401_UNAUTHORIZED = 401

    status = _StatusFallback()
else:
    _FASTAPI_IMPORT_ERROR = None

try:
    from jose import JWTError, jwt
except ModuleNotFoundError as exc:
    if exc.name != "jose":
        raise
    _JOSE_IMPORT_ERROR = exc

    class JWTError(Exception):  # type: ignore[no-redef]
        pass

    jwt: Any = None
else:
    _JOSE_IMPORT_ERROR = None

from codey.saas.config import settings


def _require_jose() -> None:
    if _JOSE_IMPORT_ERROR is not None:
        raise RuntimeError(
            "python-jose is required for JWT auth"
        ) from _JOSE_IMPORT_ERROR


def _coerce_non_empty_jwt_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        return None
    return normalized or None


def normalize_access_token_candidate(value: object) -> str | None:
    return _coerce_non_empty_jwt_text(value)


def _coerce_jwt_numeric_date(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        timestamp = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        timestamp = int(value)
    else:
        return None
    if timestamp < 0:
        return None
    return timestamp


def create_access_token(
    user_id: str,
    expires_delta: timedelta | None = None,
    *,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT with ``sub=user_id`` and an expiration claim."""
    subject = _coerce_non_empty_jwt_text(user_id)
    if subject is None:
        raise ValueError("Access token subject cannot be empty")

    _require_jose()
    assert jwt is not None

    now = datetime.utcnow()
    token_lifetime = (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.jwt_expire_minutes)
    )
    expire = now + token_lifetime
    payload = {
        "sub": subject,
        "exp": expire,
        "iat": now,
    }
    if extra_claims:
        for claim, value in extra_claims.items():
            if claim not in {"sub", "exp", "iat"}:
                payload[claim] = value
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT.

    Returns the full payload dict on success.
    Raises ``HTTPException(401)`` if the token is invalid or expired.
    """
    token = normalize_access_token_candidate(token)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _require_jose()
    assert jwt is not None

    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    subject = _coerce_non_empty_jwt_text(payload.get("sub"))
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expiration = payload.get("exp")
    if expiration is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing expiration claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    normalized_expiration = _coerce_jwt_numeric_date(expiration)
    if normalized_expiration is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload["sub"] = subject
    payload["exp"] = normalized_expiration
    return payload
