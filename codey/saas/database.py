from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codey.saas.config import settings

import ssl as _ssl

_ssl_context = _ssl.create_default_context()
_ssl_context.check_hostname = False
_ssl_context.verify_mode = _ssl.CERT_NONE

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
    connect_args={"ssl": _ssl_context},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
