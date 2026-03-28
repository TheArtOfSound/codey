
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from codey.saas.models.base import Base


class Export(Base):
    __tablename__ = "exports"


    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    export_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )
    destination: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(50), server_default=text("'pending'"), default="pending"
    )
    file_url: Mapped[Optional[str]] = mapped_column(Text)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column()
