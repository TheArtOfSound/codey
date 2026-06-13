
import math
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from codey.saas.billing.plans import PLANS
from codey.saas.models.base import Base
from codey.saas.security.encryption import decrypt_token, encrypt_token

if TYPE_CHECKING:
    from codey.saas.models.coding_session import CodingSession
    from codey.saas.models.credit_transaction import CreditTransaction
    from codey.saas.models.repository import Repository


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False
    )
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    github_id: Mapped[Optional[str]] = mapped_column(String(100))
    _github_token_ciphertext: Mapped[Optional[str]] = mapped_column("github_token", Text)
    byok_provider: Mapped[Optional[str]] = mapped_column(String(50))
    byok_model: Mapped[Optional[str]] = mapped_column(String(120))
    _byok_api_key_ciphertext: Mapped[Optional[str]] = mapped_column("byok_api_key", Text)

    @property
    def byok_api_key(self) -> Optional[str]:
        if not self._byok_api_key_ciphertext:
            return None
        try:
            return decrypt_token(self._byok_api_key_ciphertext)
        except Exception:
            return None

    @byok_api_key.setter
    def byok_api_key(self, value: Optional[str]) -> None:
        self._byok_api_key_ciphertext = encrypt_token(value) if value else None
    google_id: Mapped[Optional[str]] = mapped_column(String(100))
    name: Mapped[Optional[str]] = mapped_column(String(255))
    avatar_url: Mapped[Optional[str]] = mapped_column(Text)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String(100), unique=True
    )
    plan: Mapped[str] = mapped_column(
        String(50), server_default=text("'free'"), default="free"
    )
    plan_status: Mapped[str] = mapped_column(
        String(50), server_default=text("'active'"), default="active"
    )
    subscription_id: Mapped[Optional[str]] = mapped_column(String(100))
    subscription_period_end: Mapped[Optional[datetime]] = mapped_column()
    credits_remaining: Mapped[int] = mapped_column(
        Integer, server_default=text("10"), default=10
    )
    credits_used_this_month: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    topup_credits: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    last_active: Mapped[Optional[datetime]] = mapped_column(
        server_default=text("now()")
    )

    # Relationships
    credit_transactions: Mapped[list["CreditTransaction"]] = relationship(
        "CreditTransaction", back_populates="user", lazy="selectin"
    )
    coding_sessions: Mapped[list["CodingSession"]] = relationship(
        "CodingSession", back_populates="user", lazy="selectin"
    )
    repositories: Mapped[list["Repository"]] = relationship(
        "Repository", back_populates="user", lazy="selectin"
    )

    @property
    def total_credits(self) -> int:
        return self._coerce_credit_value(self.credits_remaining) + self._coerce_credit_value(
            self.topup_credits
        )

    @property
    def github_token(self) -> Optional[str]:
        if not self._github_token_ciphertext:
            return None

        try:
            value = decrypt_token(self._github_token_ciphertext)
        except Exception:
            # Support legacy plaintext rows until they are overwritten.
            value = self._github_token_ciphertext

        if not isinstance(value, str):
            return None

        normalized = value.strip()
        return normalized or None

    @github_token.setter
    def github_token(self, value: Optional[str]) -> None:
        if not isinstance(value, str):
            self._github_token_ciphertext = None
            return

        normalized = value.strip()
        if not normalized:
            self._github_token_ciphertext = None
            return

        self._github_token_ciphertext = encrypt_token(normalized)

    @property
    def plan_display_name(self) -> str:
        plan = self.plan
        if not isinstance(plan, str):
            return "Free"
        normalized = plan.strip().lower()
        if not normalized:
            return "Free"
        configured_name = PLANS.get(normalized, {}).get("name")
        if isinstance(configured_name, str) and configured_name.strip():
            return configured_name.strip()
        return normalized.capitalize()

    @property
    def is_pro_or_above(self) -> bool:
        plan = self.plan
        if not isinstance(plan, str):
            return False

        normalized = plan.strip().lower()
        if not normalized:
            return False

        # Preserve legacy enterprise access until that tier is formalized in PLANS.
        if normalized == "enterprise":
            return True

        features = PLANS.get(normalized, {}).get("features")
        if not isinstance(features, dict):
            return False

        return self._coerce_feature_bool(features.get("autonomous_mode"), False)

    @staticmethod
    def _coerce_feature_bool(value: object, fallback: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            normalized = float(value)
            return bool(normalized) if math.isfinite(normalized) else fallback
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True
            if normalized in {"false", "0", "no", "n", "off", ""}:
                return False
        return fallback

    @staticmethod
    def _coerce_credit_value(value: object) -> int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return 0
            try:
                return int(normalized)
            except ValueError:
                return 0
        return 0
