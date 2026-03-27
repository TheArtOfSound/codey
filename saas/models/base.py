from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        server_default=text("now()"),
        onupdate=datetime.utcnow,
        nullable=True,
    )
