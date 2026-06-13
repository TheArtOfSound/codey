from __future__ import annotations

from collections.abc import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Any
import uuid
import os

import ssl as _ssl

try:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
except ModuleNotFoundError as exc:
    if exc.name != "sqlalchemy":
        raise
    _SQLALCHEMY_IMPORT_ERROR = exc
    text = None  # type: ignore[assignment]
    AsyncSession = Any  # type: ignore[misc, assignment]
    async_sessionmaker = None  # type: ignore[assignment]
    create_async_engine = None  # type: ignore[assignment]
else:
    _SQLALCHEMY_IMPORT_ERROR = None

from codey.saas.config import settings


def _normalized_database_url() -> tuple[str, str]:
    if not isinstance(settings.database_url, str):
        return "", ""
    database_url = settings.database_url.strip()
    if not database_url:
        return "", ""
    if any(ord(char) < 32 or ord(char) == 127 for char in database_url):
        return "", ""
    if any(char.isspace() for char in database_url):
        return "", ""

    parsed = urlparse(database_url)
    try:
        port = parsed.port
    except ValueError:
        return "", ""
    if port is not None and port <= 0:
        return "", ""
    if parsed.fragment:
        return "", ""

    if not parsed.hostname:
        return "", ""

    if parsed.scheme in {"postgres", "postgresql"}:
        parsed = parsed._replace(scheme="postgresql+asyncpg")
    elif parsed.scheme != "postgresql+asyncpg":
        return "", ""
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    sslmode = next(
        (
            value.strip().lower()
            for key, value in query_pairs
            if key == "sslmode" and value.strip()
        ),
        "",
    )
    normalized_query = urlencode(
        [(key, value) for key, value in query_pairs if key != "sslmode"],
        doseq=True,
    )
    normalized_url = urlunparse(parsed._replace(query=normalized_query))
    return normalized_url, sslmode


def _build_connect_args(sslmode: str) -> dict[str, object]:
    connect_args: dict[str, object] = {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    }

    insecure_skip_verify = (
        os.environ.get("CODEY_DB_SSL_INSECURE_SKIP_VERIFY", "").strip().lower() == "true"
    )

    if sslmode == "require":
        ssl_context = _ssl.create_default_context()
        if insecure_skip_verify:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = _ssl.CERT_NONE
        connect_args["ssl"] = ssl_context
    elif sslmode in {"verify-ca", "verify-full"}:
        connect_args["ssl"] = _ssl.create_default_context()

    return connect_args


def _require_database_url(database_url: str) -> str:
    if not database_url:
        raise RuntimeError("DATABASE_URL must not be blank")
    return database_url


def _normalize_db_user_context_id(user_id: object) -> str:
    if isinstance(user_id, uuid.UUID):
        return str(user_id)
    if isinstance(user_id, str):
        value = user_id.strip()
        if value:
            try:
                return str(uuid.UUID(value))
            except ValueError:
                pass
    raise ValueError("Database user context requires a valid UUID")


_database_url, _sslmode = _normalized_database_url()

if _SQLALCHEMY_IMPORT_ERROR is None:
    _database_url = _require_database_url(_database_url)

    engine = create_async_engine(
        _database_url,
        echo=False,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        connect_args=_build_connect_args(_sslmode),
    )

    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
else:
    engine = None
    async_session_factory = None


def _require_sqlalchemy() -> None:
    if _SQLALCHEMY_IMPORT_ERROR is not None:
        raise RuntimeError(
            "SQLAlchemy is required for database access"
        ) from _SQLALCHEMY_IMPORT_ERROR


async def clear_db_user_context(session: AsyncSession) -> None:
    _require_sqlalchemy()
    assert text is not None
    await session.execute(text("SELECT set_config('app.current_user_id', '', true)"))


async def set_db_user_context(session: AsyncSession, user_id: str) -> None:
    _require_sqlalchemy()
    assert text is not None
    await session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": _normalize_db_user_context_id(user_id)},
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    _require_sqlalchemy()
    assert async_session_factory is not None
    async with async_session_factory() as session:
        try:
            await clear_db_user_context(session)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
