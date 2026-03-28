
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Abstract base. Each model defines its own PK and timestamp columns
    to match its specific database schema."""

    __abstract__ = True
