from __future__ import annotations

# !! MUST run before any other imports that read env vars !!
import os as _os
from pathlib import Path as _Path
import re as _re

_MAX_SECRET_ENV_CHARS = 1_000_000
_ENV_KEY_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _read_secret_env_lines(path: _Path) -> list[str] | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as _handle:
            _content = _handle.read(_MAX_SECRET_ENV_CHARS + 1)
    except AttributeError:
        try:
            _content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
    except OSError:
        return None
    if len(_content) > _MAX_SECRET_ENV_CHARS:
        return None
    return _content.splitlines()


def _load_secret_env_file(path: _Path) -> None:
    if not path.exists():
        return
    lines = _read_secret_env_lines(path)
    if lines is None:
        return
    for _line in lines:
        _line = _line.strip()
        if _line and "=" in _line and not _line.startswith("#"):
            _k, _, _v = _line.partition("=")
            _key = _k.strip()
            if not _key or not _ENV_KEY_RE.fullmatch(_key):
                continue
            try:
                _os.environ[_key] = _v.strip()
            except (OSError, ValueError):
                continue


_secret_env = _Path("/etc/secrets/.env")
_load_secret_env_file(_secret_env)
# !! End secret file loading !!

import os
import asyncio
import inspect
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
import logging
import math
import re
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from codey.saas.billing.stripe_setup import setup_stripe_products
from codey.saas.config import settings
from codey.saas.database import engine
from codey.saas.database_bootstrap import ensure_database_compatibility
from codey.saas.redis_url import normalize_redis_url
from codey.saas.security.middleware import SecurityMiddleware

_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_URL_CREDENTIALS_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_QUERY_SECRET_RE = re.compile(
    r"([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token)=)[^&#\s]+",
    re.IGNORECASE,
)
_NAMED_SECRET_RE = re.compile(
    r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token|authorization)\b(\s*[:=]\s*)"
    r"(?:Bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_non_empty_env_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if _has_ascii_control(normalized):
        return None
    return normalized or None


def _coerce_sentry_traces_sample_rate(value: str | None, default: float = 0.1) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, min(1.0, parsed))


def _coerce_sentry_dsn(value: object) -> str | None:
    dsn = _coerce_non_empty_env_text(value if isinstance(value, str) else None)
    if dsn is None or _has_whitespace(dsn):
        return None
    try:
        parsed = urlparse(dsn)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.netloc or not parsed.hostname:
        return None
    if parsed.password is not None:
        return None
    if port is not None and port <= 0:
        return None
    if parsed.fragment:
        return None
    return dsn


def _health_redis_url() -> str:
    redis_url = _coerce_non_empty_env_text(settings.redis_url)
    if redis_url:
        return normalize_redis_url(redis_url) or _DEFAULT_REDIS_URL
    return _DEFAULT_REDIS_URL


def _redact_connection_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIALS_RE.sub(r"\1***@", text)
    text = _QUERY_SECRET_RE.sub(r"\1***", text)

    def _replace_named_secret(match: re.Match[str]) -> str:
        prefix = f"{match.group(1)}{match.group(2)}"
        if "bearer" in match.group(0).lower():
            return f"{prefix}Bearer ***"
        return f"{prefix}***"

    text = _NAMED_SECRET_RE.sub(_replace_named_secret, text)
    return _EMAIL_RE.sub("[redacted-email]", text)


def _secret_key_requires_production_guard(value: object) -> bool:
    normalized = _coerce_non_empty_env_text(value if isinstance(value, str) else None)
    return (normalized or "change-me-in-production") == "change-me-in-production"


async def _run_lifespan_cleanup(name: str, cleanup) -> None:
    try:
        result = cleanup()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        logging.getLogger("codey").warning(
            "Shutdown cleanup failed for %s: %s",
            name,
            _redact_connection_error(exc),
        )


# ---------------------------------------------------------------------------
# Sentry (conditional on SENTRY_DSN)
# ---------------------------------------------------------------------------
_sentry_dsn = _coerce_sentry_dsn(os.environ.get("SENTRY_DSN"))
if _sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        traces_sample_rate=_coerce_sentry_traces_sample_rate(
            os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")
        ),
        environment=_coerce_non_empty_env_text(os.environ.get("SENTRY_ENVIRONMENT"))
        or "production",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    codey_env = (_coerce_non_empty_env_text(os.environ.get("CODEY_ENV")) or "development").lower()
    if codey_env == "production" and _secret_key_requires_production_guard(settings.secret_key):
        raise RuntimeError("SECRET_KEY must be set to a non-default value in production")

    await ensure_database_compatibility(engine)

    bootstrap_stripe = _coerce_non_empty_env_text(
        os.environ.get("CODEY_BOOTSTRAP_STRIPE_ON_STARTUP")
    )
    if (bootstrap_stripe or "").lower() == "true":
        try:
            await setup_stripe_products()
        except Exception as exc:
            logging.getLogger("codey").warning(
                "Stripe setup skipped: %s",
                _redact_connection_error(exc),
            )

    try:
        yield
    finally:
        from codey.saas.intelligence.cache import close as close_intelligence_cache
        from codey.saas.intelligence.embeddings import embedding_service
        from codey.saas.intelligence.services import intelligence_services

        cleanup_tasks = (
            ("intelligence cache", close_intelligence_cache),
            ("intelligence services", intelligence_services.close),
            ("embedding service", embedding_service.close),
        )
        await asyncio.gather(
            *(_run_lifespan_cleanup(name, cleanup) for name, cleanup in cleanup_tasks)
        )


app = FastAPI(title="Codey API", version="1.0.0", lifespan=lifespan)


def _coerce_non_empty_origin(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or _has_ascii_control(normalized)
        or _has_whitespace(normalized)
    ):
        return None

    parsed = urlparse(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and port <= 0:
        return None

    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")


def _cors_allowed_origins() -> list[str]:
    origins = {
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://codey.imagineqira.com",
    }
    frontend_origin = _coerce_non_empty_origin(settings.frontend_url)
    if frontend_origin:
        origins.add(frontend_origin)

    extra_origins = os.environ.get("CODEY_CORS_ORIGINS", "")
    for origin in extra_origins.split(","):
        stripped = _coerce_non_empty_origin(origin)
        if stripped:
            origins.add(stripped)

    return sorted(origin for origin in origins if _coerce_non_empty_origin(origin))


app.add_middleware(SecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Basic liveness check."""
    return {"status": "ok"}


@app.get("/health/db", tags=["health"])
async def health_db() -> JSONResponse:
    """Check database connectivity."""
    try:
        from sqlalchemy import text
        from codey.saas.database import async_session_factory

        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return JSONResponse({"status": "ok", "database": "connected"})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "database": _redact_connection_error(exc)},
            status_code=503,
        )


@app.get("/health/redis", tags=["health"])
async def health_redis() -> JSONResponse:
    """Check Redis connectivity."""
    r = None
    try:
        import redis.asyncio as aioredis
        from codey.saas.intelligence.cache import close_redis_client

        r = aioredis.from_url(_health_redis_url(), decode_responses=True)
        pong = await r.ping()
        return JSONResponse({"status": "ok", "redis": "connected", "ping": pong})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "redis": _redact_connection_error(exc)},
            status_code=503,
        )
    finally:
        if r is not None:
            try:
                await close_redis_client(r)
            except Exception as exc:
                logging.getLogger("codey").warning(
                    "Redis health cleanup failed: %s",
                    _redact_connection_error(exc),
                )


# -- mount routers -----------------------------------------------------------
from codey.saas.api.auth_routes import router as auth_router  # noqa: E402
from codey.saas.api.user_routes import router as user_router  # noqa: E402
from codey.saas.api.session_routes import router as session_router  # noqa: E402
from codey.saas.api.repo_routes import router as repo_router  # noqa: E402
from codey.saas.api.billing_routes import router as billing_router  # noqa: E402
from codey.saas.api.credit_routes import router as credit_router  # noqa: E402
from codey.saas.api.admin_routes import router as admin_router  # noqa: E402
from codey.saas.api.referral_routes import router as referral_router  # noqa: E402
from codey.saas.api.build_routes import router as build_router  # noqa: E402
from codey.saas.api.github_routes import router as github_router  # noqa: E402
from codey.saas.api.health_analysis import router as health_analysis_router  # noqa: E402
from codey.saas.api.memory_routes import router as memory_router  # noqa: E402
from codey.saas.api.vault_routes import router as vault_router  # noqa: E402

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(session_router)
app.include_router(repo_router)
app.include_router(billing_router)
app.include_router(credit_router)
app.include_router(admin_router)
app.include_router(referral_router)
app.include_router(build_router)
app.include_router(github_router)
app.include_router(health_analysis_router)
app.include_router(memory_router)
app.include_router(vault_router)
