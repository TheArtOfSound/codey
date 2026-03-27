from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from codey.saas.models.base import Base


class MemoryUpdateLog(Base):
    __tablename__ = "memory_update_logs"

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
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coding_sessions.id"), nullable=True
    )
    update_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )
    field_updated: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    previous_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_confidence: Mapped[float | None] = mapped_column()
    source_description: Mapped[str | None] = mapped_column(Text)
    memory_version_before: Mapped[int | None] = mapped_column(Integer)
    memory_version_after: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
