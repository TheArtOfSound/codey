from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from codey.saas.models.base import Base

if TYPE_CHECKING:
    from codey.saas.models.user import User


class Repository(Base):
    __tablename__ = "repositories"

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
    github_repo_id: Mapped[int | None] = mapped_column(Integer)
    full_name: Mapped[str | None] = mapped_column(String(255))
    clone_url: Mapped[str | None] = mapped_column(Text)
    default_branch: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str | None] = mapped_column(String(100))
    autonomous_mode_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    autonomous_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_analyzed: Mapped[datetime | None] = mapped_column()
    nfet_phase: Mapped[str | None] = mapped_column(String(20))
    es_score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="repositories")
