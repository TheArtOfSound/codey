
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from codey.saas.models.base import Base

if TYPE_CHECKING:
    from codey.saas.models.coding_session import CodingSession
    from codey.saas.models.credit_transaction import CreditTransaction
    from codey.saas.models.repository import Repository


class User(Base):
    __tablename__ = "users"

    # Remove inherited updated_at since schema doesn't include it
    updated_at: Mapped[None] = None  # type: ignore[assignment]

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
    github_token: Mapped[Optional[str]] = mapped_column(Text)
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
        return self.credits_remaining + self.topup_credits

    @property
    def plan_display_name(self) -> str:
        plan_names = {
            "free": "Free",
            "pro": "Pro",
            "team": "Team",
            "enterprise": "Enterprise",
        }
        return plan_names.get(self.plan, self.plan.capitalize())

    @property
    def is_pro_or_above(self) -> bool:
        return self.plan in ("pro", "team", "enterprise")
