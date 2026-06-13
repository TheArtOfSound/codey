from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
from uuid import UUID

import bcrypt
import stripe
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.jwt import create_access_token, decode_access_token
from codey.saas.auth.oauth import (
    _build_callback_url,
    _coerce_oauth_avatar_url,
    decode_oauth_state,
    exchange_github_code,
    exchange_google_code,
)
from codey.saas.config import settings
from codey.saas.models import User

logger = logging.getLogger("codey")
_PASSWORD_RESET_PURPOSE = "password_reset"
_BCRYPT_MAX_PASSWORD_BYTES = 72
_URL_CREDENTIAL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|token|secret|password)=)[^&\s]+"
)
_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|token|secret|password|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


def _redact_auth_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _NAMED_SECRET_RE.sub(r"\1***", text)
    return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)


def _coerce_non_empty_auth_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _auth_text_update(current: object, incoming: object) -> str | None:
    incoming_text = _coerce_non_empty_auth_text(incoming)
    if incoming_text is None:
        return None
    return incoming_text if _coerce_non_empty_auth_text(current) is None else None


def _coerce_auth_subject(value: object) -> str | None:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    subject = value.strip()
    if subject == "__invalid__":
        return None
    return subject or None


# Configure the Stripe library once at import time
stripe.api_key = _coerce_non_empty_auth_text(settings.stripe_secret_key) or ""


class AuthService:
    """Handles signup, login, OAuth callbacks, and password reset flows."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_password(password: str) -> str:
        if len(password.encode("utf-8")) > _BCRYPT_MAX_PASSWORD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be 72 bytes or fewer",
            )
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    @staticmethod
    def _verify_password(plain: str, hashed: str) -> bool:
        try:
            return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
        except ValueError:
            return False

    @staticmethod
    def _make_token(user: User) -> str:
        user_id = _coerce_auth_subject(getattr(user, "id", None))
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to create access token",
            )
        return create_access_token(user_id)

    async def _get_user_by_email(self, email: str) -> User | None:
        result = await self.db.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def _create_stripe_customer(self, email: str, name: str | None) -> str | None:
        """Create a Stripe customer and return the customer ID. Returns None if Stripe is not configured."""
        try:
            customer = stripe.Customer.create(
                email=email,
                name=name or "",
                metadata={"source": "codey_signup"},
            )
            return customer["id"]
        except Exception as e:
            logger.warning(
                "Stripe customer creation skipped: %s",
                _redact_auth_error(e),
            )
            return None

    # ------------------------------------------------------------------
    # Email/password auth
    # ------------------------------------------------------------------

    async def signup(
        self,
        email: str,
        password: str,
        name: str | None = None,
        *,
        frontend_origin: str | None = None,
    ) -> tuple[User, str]:
        """Register a new user with email and password.

        Returns the created ``User`` and a JWT access token.
        """
        existing = await self._get_user_by_email(email)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An account with this email already exists",
            )

        password_hash = self._hash_password(password)

        user = User(
            email=email,
            password_hash=password_hash,
            name=name,
            plan="free",
            credits_remaining=10,
        )
        self.db.add(user)
        await self.db.flush()  # Populate user.id

        stripe_customer_id = await self._create_stripe_customer(email, name)
        user.stripe_customer_id = stripe_customer_id
        await self.db.flush()

        token = self._make_token(user)

        # Send welcome email (best-effort)
        try:
            from codey.saas.emails.service import EmailService

            email_svc = EmailService()
            await email_svc.send_welcome(
                email,
                name or email,
                frontend_origin=frontend_origin,
            )
        except Exception as exc:
            logger.debug("Welcome email skipped: %s", _redact_auth_error(exc))

        return user, token

    async def login(self, email: str, password: str) -> tuple[User, str]:
        """Authenticate with email and password.

        Returns the ``User`` and a JWT access token.
        """
        user = await self._get_user_by_email(email)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        password_hash = _coerce_non_empty_auth_text(
            getattr(user, "password_hash", None)
        )
        if password_hash is None or not self._verify_password(password, password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        user.last_active = datetime.utcnow()
        await self.db.flush()

        token = self._make_token(user)
        return user, token

    # ------------------------------------------------------------------
    # OAuth flows
    # ------------------------------------------------------------------

    async def github_callback(self, code: str, state: str) -> tuple[User, str]:
        """Handle the GitHub OAuth callback.

        Exchanges the authorization code, finds or creates the user, and
        returns the ``User`` with a JWT.
        """
        try:
            state_data = decode_oauth_state(state, "github")
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_redact_auth_error(exc),
            ) from exc

        gh_info = await exchange_github_code(
            code,
            redirect_uri=_build_callback_url("github", state_data.get("api_base_url")),
        )
        github_email = _coerce_non_empty_auth_text(gh_info.get("email"))
        github_avatar_url = _coerce_oauth_avatar_url(gh_info.get("avatar_url"))

        # Look up by github_id first, then by email
        result = await self.db.execute(
            select(User).where(User.github_id == gh_info["id"])
        )
        user = result.scalar_one_or_none()

        if user is None and github_email:
            user = await self._get_user_by_email(github_email)

        if user is None:
            # New user via GitHub
            user = User(
                email=github_email or f"gh-{gh_info['id']}@users.noreply.github.com",
                github_id=gh_info["id"],
                github_token=gh_info["access_token"],
                name=gh_info.get("name"),
                avatar_url=github_avatar_url,
                plan="free",
                credits_remaining=10,
            )
            self.db.add(user)
            await self.db.flush()

            stripe_customer_id = await self._create_stripe_customer(
                user.email, user.name
            )
            user.stripe_customer_id = stripe_customer_id
        else:
            # Existing user — link/update GitHub info
            user.github_id = gh_info["id"]
            user.github_token = gh_info["access_token"]
            if name := _auth_text_update(
                getattr(user, "name", None),
                gh_info.get("name"),
            ):
                user.name = name
            if avatar_url := _auth_text_update(
                getattr(user, "avatar_url", None),
                github_avatar_url,
            ):
                user.avatar_url = avatar_url

        user.last_active = datetime.utcnow()
        await self.db.flush()

        token = self._make_token(user)
        return user, token

    async def google_callback(self, code: str, state: str) -> tuple[User, str]:
        """Handle the Google OAuth callback.

        Exchanges the authorization code, finds or creates the user, and
        returns the ``User`` with a JWT.
        """
        try:
            state_data = decode_oauth_state(state, "google")
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_redact_auth_error(exc),
            ) from exc

        google_info = await exchange_google_code(
            code,
            redirect_uri=_build_callback_url("google", state_data.get("api_base_url")),
        )
        google_avatar_url = _coerce_oauth_avatar_url(google_info.get("avatar_url"))

        # Look up by google_id first, then by email
        result = await self.db.execute(
            select(User).where(User.google_id == google_info["id"])
        )
        user = result.scalar_one_or_none()

        if user is None and google_info.get("email"):
            user = await self._get_user_by_email(google_info["email"])

        if user is None:
            # New user via Google
            user = User(
                email=google_info["email"],
                google_id=google_info["id"],
                name=google_info.get("name"),
                avatar_url=google_avatar_url,
                plan="free",
                credits_remaining=10,
            )
            self.db.add(user)
            await self.db.flush()

            stripe_customer_id = await self._create_stripe_customer(
                user.email, user.name
            )
            user.stripe_customer_id = stripe_customer_id
        else:
            # Existing user — link/update Google info
            user.google_id = google_info["id"]
            if name := _auth_text_update(
                getattr(user, "name", None),
                google_info.get("name"),
            ):
                user.name = name
            if avatar_url := _auth_text_update(
                getattr(user, "avatar_url", None),
                google_avatar_url,
            ):
                user.avatar_url = avatar_url

        user.last_active = datetime.utcnow()
        await self.db.flush()

        token = self._make_token(user)
        return user, token

    # ------------------------------------------------------------------
    # Password reset
    # ------------------------------------------------------------------

    async def request_password_reset(
        self,
        email: str,
        *,
        frontend_origin: str | None = None,
    ) -> None:
        """Generate and deliver a short-lived password-reset token."""
        user = await self._get_user_by_email(email)
        if user is None:
            return

        user_id = _coerce_auth_subject(getattr(user, "id", None))
        user_email = _coerce_non_empty_auth_text(getattr(user, "email", None))
        if user_id is None or user_email is None:
            return

        token = create_access_token(
            user_id,
            expires_delta=timedelta(hours=1),
            extra_claims={"purpose": _PASSWORD_RESET_PURPOSE},
        )
        try:
            from codey.saas.emails.service import EmailService

            email_svc = EmailService()
            await email_svc.send_password_reset(
                user_email,
                token,
                frontend_origin=frontend_origin,
            )
        except Exception as exc:
            logger.warning(
                "Password reset email skipped for %s: %s",
                _redact_auth_error(user_email),
                _redact_auth_error(exc),
            )

    async def reset_password(self, token: str, new_password: str) -> bool:
        """Validate a reset token and update the user's password.

        Returns ``True`` on success, ``False`` if the token is invalid or the
        user no longer exists.
        """
        try:
            payload = decode_access_token(token)
        except HTTPException:
            return False
        if payload.get("purpose") != _PASSWORD_RESET_PURPOSE:
            return False

        user_id = _coerce_auth_subject(payload.get("sub"))
        if user_id is None:
            return False

        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return False

        user.password_hash = self._hash_password(new_password)
        await self.db.flush()
        return True
