from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from codey.saas.models.base import Base

if TYPE_CHECKING:
    from codey.saas.models.user import User


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    # Remove inherited updated_at since schema doesn't include it
    updated_at: Mapped[None] = None  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(100))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    credits_before: Mapped[int | None] = mapped_column(Integer)
    credits_after: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="credit_transactions")
