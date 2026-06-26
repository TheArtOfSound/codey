from __future__ import annotations

import logging
import os
import time
import traceback

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------


def _coerce_non_empty_security_env_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        return None
    return normalized or None


_IS_PRODUCTION = (
    (_coerce_non_empty_security_env_text(os.getenv("CODEY_ENV")) or "development").lower()
    == "production"
)

# ---------------------------------------------------------------------------
# Content-Security-Policy — restrictive default, relax per-route as needed.
# ---------------------------------------------------------------------------

_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https:",
        "font-src 'self'",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)


class SecurityMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that hardens every HTTP response and logs requests.

    *   Sets standard security headers (HSTS, CSP, X-Frame-Options, etc.)
    *   Logs request metadata to the Python logger (user_id when available,
        IP, user-agent, path, method, status, duration).
    *   Strips stack traces and internal details from error responses in
        production.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    @staticmethod
    def _apply_security_headers(response: Response) -> Response:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        return response

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        start = time.monotonic()

        # ------------------------------------------------------------------
        # Execute the downstream handler
        # ------------------------------------------------------------------
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.monotonic() - start) * 1000
            self._log_request(request, 500, duration_ms)
            if _IS_PRODUCTION:
                return self._apply_security_headers(
                    Response(
                        content='{"detail":"Internal server error"}',
                        status_code=500,
                        media_type="application/json",
                    )
                )
            raise

        duration_ms = (time.monotonic() - start) * 1000

        # ------------------------------------------------------------------
        # Security headers
        # ------------------------------------------------------------------
        response = self._apply_security_headers(response)

        # ------------------------------------------------------------------
        # Strip sensitive details from error responses in production
        # ------------------------------------------------------------------
        if _IS_PRODUCTION and response.status_code >= 500:
            # We can't rewrite an already-streaming response easily, but we
            # log the real error above and the exception handler returns a
            # sanitised body.
            pass

        # ------------------------------------------------------------------
        # Audit-trail logging
        # ------------------------------------------------------------------
        self._log_request(request, response.status_code, duration_ms)

        return response

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_user_id(request: Request) -> str | None:
        """Best-effort extraction of user_id from the Authorization header.

        Returns ``None`` if no valid JWT is present — this is expected for
        unauthenticated endpoints.
        """
        try:
            from codey.saas.auth.cookies import SESSION_COOKIE_NAME
            from codey.saas.auth.jwt import decode_access_token, normalize_access_token_candidate
        except Exception:
            return None

        auth = request.headers.get("authorization", "")
        candidates: list[object] = []
        if isinstance(auth, str):
            auth_parts = auth.strip().split(None, 1)
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
            subject = normalize_access_token_candidate(payload.get("sub"))
            if subject is not None:
                return subject

        return None

    @staticmethod
    def _log_request(
        request: Request,
        status_code: int,
        duration_ms: float,
    ) -> None:
        user_id = SecurityMiddleware._extract_user_id(request)
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "")

        logger.info(
            "request",
            extra={
                "user_id": user_id,
                "ip": client_ip,
                "user_agent": user_agent,
                "method": request.method,
                "path": request.url.path,
                "status": status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
