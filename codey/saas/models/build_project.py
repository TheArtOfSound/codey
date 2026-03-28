
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from codey.saas.models.base import Base

if TYPE_CHECKING:
    from codey.saas.models.build_checkpoint import BuildCheckpoint
    from codey.saas.models.build_file import BuildFile
    from codey.saas.models.coding_session import CodingSession
    from codey.saas.models.user import User


class BuildProject(Base):
    __tablename__ = "build_projects"


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
        UUID(as_uuid=True), ForeignKey("coding_sessions.id")
    )
    name: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(50), server_default=text("'planning'"), default="planning"
    )
    current_phase: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    total_phases: Mapped[Optional[int]] = mapped_column(Integer)
    files_planned: Mapped[Optional[int]] = mapped_column(Integer)
    files_completed: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    lines_generated: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    credits_charged: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    nfet_es_score_final: Mapped[Optional[float]] = mapped_column(Float)
    nfet_phase_final: Mapped[Optional[str]] = mapped_column(String(20))
    project_plan: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    file_tree: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    stack: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    download_url: Mapped[Optional[str]] = mapped_column(Text)
    github_repo_url: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column()

    # Relationships
    user: Mapped["User"] = relationship("User", lazy="selectin")
    session: Mapped[Optional["CodingSession"]] = relationship(
        "CodingSession", lazy="selectin"
    )
    files: Mapped[list["BuildFile"]] = relationship(
        "BuildFile", back_populates="project", lazy="selectin",
        cascade="all, delete-orphan",
    )
    checkpoints: Mapped[list["BuildCheckpoint"]] = relationship(
        "BuildCheckpoint", back_populates="project", lazy="selectin",
        cascade="all, delete-orphan",
    )
