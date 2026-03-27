from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from codey.saas.models.base import Base


class SessionCost(Base):
    __tablename__ = "session_costs"

    # Remove inherited updated_at since schema doesn't include it
    updated_at: Mapped[None] = None  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coding_sessions.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    api_cost_usd: Mapped[float | None] = mapped_column(Float)
    credits_charged: Mapped[int | None] = mapped_column(Integer)
    margin_ratio: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
