from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ARRAY, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from codey.saas.models.base import Base

if TYPE_CHECKING:
    from codey.saas.models.user import User


class CodingSession(Base):
    __tablename__ = "coding_sessions"

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
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text)
    files_uploaded: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    repo_connected: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(50), server_default=text("'pending'"), default="pending"
    )
    credits_charged: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    lines_generated: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    files_modified: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    nfet_phase_before: Mapped[str | None] = mapped_column(String(20))
    nfet_phase_after: Mapped[str | None] = mapped_column(String(20))
    es_score_before: Mapped[float | None] = mapped_column(Float)
    es_score_after: Mapped[float | None] = mapped_column(Float)
    output_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column()

    # Remove inherited created_at — this model uses started_at instead
    created_at: Mapped[None] = None  # type: ignore[assignment]

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="coding_sessions")
