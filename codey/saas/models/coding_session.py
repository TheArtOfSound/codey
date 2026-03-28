
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ARRAY, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from codey.saas.models.base import Base

if TYPE_CHECKING:
    from codey.saas.models.user import User


class CodingSession(Base):
    __tablename__ = "coding_sessions"


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
    prompt: Mapped[Optional[str]] = mapped_column(Text)
    files_uploaded: Mapped[Optional[list]] = mapped_column(ARRAY(String))
    repo_connected: Mapped[Optional[str]] = mapped_column(String(255))
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
    nfet_phase_before: Mapped[Optional[str]] = mapped_column(String(20))
    nfet_phase_after: Mapped[Optional[str]] = mapped_column(String(20))
    es_score_before: Mapped[Optional[float]] = mapped_column(Float)
    es_score_after: Mapped[Optional[float]] = mapped_column(Float)
    output_summary: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column()


    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="coding_sessions")
