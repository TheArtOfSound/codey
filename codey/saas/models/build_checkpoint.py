
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from codey.saas.models.base import Base

if TYPE_CHECKING:
    from codey.saas.models.build_project import BuildProject


class BuildCheckpoint(Base):
    __tablename__ = "build_checkpoints"

    # Remove inherited updated_at and created_at — this model uses checkpoint_at
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
    phase: Mapped[Optional[int]] = mapped_column(Integer)
    phase_name: Mapped[Optional[str]] = mapped_column(String(255))
    files_in_phase: Mapped[Optional[int]] = mapped_column(Integer)
    tests_passed: Mapped[Optional[int]] = mapped_column(Integer)
    tests_failed: Mapped[Optional[int]] = mapped_column(Integer)
    nfet_es_score: Mapped[Optional[float]] = mapped_column(Float)
    nfet_kappa: Mapped[Optional[float]] = mapped_column(Float)
    nfet_sigma: Mapped[Optional[float]] = mapped_column(Float)
    user_action: Mapped[Optional[str]] = mapped_column(String(50))
    user_notes: Mapped[Optional[str]] = mapped_column(Text)
    checkpoint_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )

    # Relationships
    project: Mapped["BuildProject"] = relationship(
        "BuildProject", back_populates="checkpoints"
    )
