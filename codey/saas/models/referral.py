
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from codey.saas.models.base import Base


class Referral(Base):
    __tablename__ = "referrals"


    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    referrer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    referred_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(50), server_default=text("'pending'"), default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    converted_at: Mapped[Optional[datetime]] = mapped_column()
    credits_issued_referrer: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    credits_issued_referred: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
