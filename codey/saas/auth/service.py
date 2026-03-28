from __future__ import annotations

from datetime import datetime, timedelta

import bcrypt
import stripe
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.jwt import create_access_token, decode_access_token
from codey.saas.auth.oauth import exchange_github_code, exchange_google_code
from codey.saas.config import settings
from codey.saas.models import User

# Configure the Stripe library once at import time
stripe.api_key = settings.stripe_secret_key


class AuthService:
    """Handles signup, login, OAuth callbacks, and password reset flows."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    @staticmethod
    def _verify_password(plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

    @staticmethod
    def _make_token(user: User) -> str:
        return create_access_token(str(user.id))

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
            import logging
            logging.getLogger("codey").warning(f"Stripe customer creation skipped: {e}")
            return None

    # ------------------------------------------------------------------
    # Email/password auth
    # ------------------------------------------------------------------

    async def signup(
        self,
        email: str,
        password: str,
        name: str | None = None,
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
            from codey.saas.emails.templates import welcome_email
            email_svc = EmailService()
            subject, html = welcome_email(name or email)
            await email_svc.send_email(email, subject, html)
        except Exception:
            import logging
            logging.getLogger("codey").debug("Welcome email skipped", exc_info=True)

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

        if user.password_hash is None or not self._verify_password(
            password, user.password_hash
        ):
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

    async def github_callback(self, code: str) -> tuple[User, str]:
        """Handle the GitHub OAuth callback.

        Exchanges the authorization code, finds or creates the user, and
        returns the ``User`` with a JWT.
        """
        gh_info = await exchange_github_code(code)

        # Look up by github_id first, then by email
        result = await self.db.execute(
            select(User).where(User.github_id == gh_info["id"])
        )
        user = result.scalar_one_or_none()

        if user is None and gh_info.get("email"):
            user = await self._get_user_by_email(gh_info["email"])

        if user is None:
            # New user via GitHub
            user = User(
                email=gh_info.get("email", f"gh-{gh_info['id']}@users.noreply.github.com"),
                github_id=gh_info["id"],
                github_token=gh_info["access_token"],
                name=gh_info.get("name"),
                avatar_url=gh_info.get("avatar_url"),
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
            if gh_info.get("name") and not user.name:
                user.name = gh_info["name"]
            if gh_info.get("avatar_url") and not user.avatar_url:
                user.avatar_url = gh_info["avatar_url"]

        user.last_active = datetime.utcnow()
        await self.db.flush()

        token = self._make_token(user)
        return user, token

    async def google_callback(self, code: str) -> tuple[User, str]:
        """Handle the Google OAuth callback.

        Exchanges the authorization code, finds or creates the user, and
        returns the ``User`` with a JWT.
        """
        google_info = await exchange_google_code(code)

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
                avatar_url=google_info.get("avatar_url"),
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
            if google_info.get("name") and not user.name:
                user.name = google_info["name"]
            if google_info.get("avatar_url") and not user.avatar_url:
                user.avatar_url = google_info["avatar_url"]

        user.last_active = datetime.utcnow()
        await self.db.flush()

        token = self._make_token(user)
        return user, token

    # ------------------------------------------------------------------
    # Password reset
    # ------------------------------------------------------------------

    async def request_password_reset(self, email: str) -> str:
        """Generate a short-lived password-reset token.

        Returns the token string. The caller is responsible for delivering it
        to the user (e.g. via email).

        If the email does not match any account, a token is still generated
        to prevent user-enumeration attacks (the caller should always show a
        generic success message).
        """
        user = await self._get_user_by_email(email)
        if user is None:
            # Return a dummy token to prevent enumeration
            return create_access_token("__invalid__", expires_delta=timedelta(hours=1))

        return create_access_token(str(user.id), expires_delta=timedelta(hours=1))

    async def reset_password(self, token: str, new_password: str) -> bool:
        """Validate a reset token and update the user's password.

        Returns ``True`` on success, ``False`` if the token is invalid or the
        user no longer exists.
        """
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id or user_id == "__invalid__":
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
