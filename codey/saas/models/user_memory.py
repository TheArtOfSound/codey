
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from codey.saas.models.base import Base


class UserMemory(Base):
    __tablename__ = "user_memory"



    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
    )
    style_model: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    work_patterns: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    project_knowledge: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    communication_style: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    structural_preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    skill_profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    explicit_preferences: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), default=list
    )
    proactive_queue: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), default=list
    )
    memory_version: Mapped[int] = mapped_column(
        Integer, server_default=text("1"), default=1
    )
    last_updated: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    total_sessions_analyzed: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
