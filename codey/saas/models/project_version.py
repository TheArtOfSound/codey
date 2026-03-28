
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from codey.saas.models.base import Base

if TYPE_CHECKING:
    from codey.saas.models.project import Project


class ProjectVersion(Base):
    __tablename__ = "project_versions"


    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coding_sessions.id"), nullable=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    commit_message: Mapped[Optional[str]] = mapped_column(Text)
    files_changed: Mapped[Optional[list]] = mapped_column(JSONB)
    diff: Mapped[Optional[str]] = mapped_column(Text)
    file_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)
    nfet_phase: Mapped[Optional[str]] = mapped_column(String(20))
    es_score: Mapped[Optional[float]] = mapped_column(Float)
    nfet_state: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )

    # Relationships
    project: Mapped["Project"] = relationship(
        "Project", back_populates="versions"
    )
