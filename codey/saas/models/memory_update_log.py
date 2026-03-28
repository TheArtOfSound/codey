
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from codey.saas.models.base import Base


class MemoryUpdateLog(Base):
    __tablename__ = "memory_update_logs"


    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coding_sessions.id"), nullable=True
    )
    update_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )
    field_updated: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    previous_value: Mapped[Optional[dict]] = mapped_column(JSONB)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB)
    extraction_confidence: Mapped[Optional[float]] = mapped_column()
    source_description: Mapped[Optional[str]] = mapped_column(Text)
    memory_version_before: Mapped[Optional[int]] = mapped_column(Integer)
    memory_version_after: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
