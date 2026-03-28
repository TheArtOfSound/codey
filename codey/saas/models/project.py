
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from codey.saas.models.base import Base

if TYPE_CHECKING:
    from codey.saas.models.project_version import ProjectVersion
    from codey.saas.models.user import User


class Project(Base):
    __tablename__ = "projects"


    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[Optional[str]] = mapped_column(String(100))
    framework: Mapped[Optional[str]] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    file_tree: Mapped[Optional[dict]] = mapped_column(JSONB)
    latest_nfet_phase: Mapped[Optional[str]] = mapped_column(String(20))
    latest_es_score: Mapped[Optional[float]] = mapped_column(Float)
    total_versions: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    total_sessions: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    last_activity: Mapped[Optional[datetime]] = mapped_column(
        server_default=text("now()")
    )

    # Relationships
    user: Mapped["User"] = relationship("User", lazy="selectin")
    versions: Mapped[list["ProjectVersion"]] = relationship(
        "ProjectVersion",
        back_populates="project",
        lazy="selectin",
        order_by="ProjectVersion.version_number.desc()",
    )
