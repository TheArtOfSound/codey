
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from codey.saas.models.base import Base

if TYPE_CHECKING:
    from codey.saas.models.build_project import BuildProject


class BuildFile(Base):
    __tablename__ = "build_files"

    # Remove inherited updated_at and created_at — this model uses generated_at
    updated_at: Mapped[None] = None  # type: ignore[assignment]
    created_at: Mapped[None] = None  # type: ignore[assignment]

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("build_projects.id"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    line_count: Mapped[Optional[int]] = mapped_column(Integer)
    phase: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(50), server_default=text("'pending'"), default="pending"
    )
    stress_score: Mapped[Optional[float]] = mapped_column(Float)
    generation_attempts: Mapped[int] = mapped_column(
        Integer, server_default=text("1"), default=1
    )
    validation_passed: Mapped[Optional[bool]] = mapped_column(Boolean)
    credits_charged: Mapped[Optional[float]] = mapped_column(Float)
    generated_at: Mapped[Optional[datetime]] = mapped_column()

    # Relationships
    project: Mapped["BuildProject"] = relationship(
        "BuildProject", back_populates="files"
    )
