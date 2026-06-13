
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from codey.saas.models.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"


    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[Optional[str]] = mapped_column(String(255))
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    key_prefix: Mapped[Optional[str]] = mapped_column(String(20))
    last_used: Mapped[Optional[datetime]] = mapped_column()
    expires_at: Mapped[Optional[datetime]] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        expires_at = self.expires_at
        if isinstance(expires_at, str):
            normalized = expires_at.strip()
            if not normalized:
                return True
            try:
                expires_at = datetime.fromisoformat(
                    normalized.replace("Z", "+00:00")
                )
            except ValueError:
                return True

        if not isinstance(expires_at, datetime):
            return True

        if expires_at.tzinfo is not None:
            return datetime.now(timezone.utc) > expires_at.astimezone(timezone.utc)
        return datetime.utcnow() > expires_at
