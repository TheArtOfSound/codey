from __future__ import annotations

import os
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from codey.saas.billing.stripe_setup import setup_stripe_products
from codey.saas.config import settings

# ---------------------------------------------------------------------------
# Sentry (conditional on SENTRY_DSN)
# ---------------------------------------------------------------------------
_sentry_dsn = os.environ.get("SENTRY_DSN")
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
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    try:
        await setup_stripe_products()
    except Exception as e:
        import logging
        logging.getLogger("codey").warning(f"Stripe setup skipped: {e}")
    yield


app = FastAPI(title="Codey API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url,
        "http://localhost:3000",
        "https://theartofsound.github.io",
    ],
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
            {"status": "error", "database": str(exc)},
            status_code=503,
        )


@app.get("/health/redis", tags=["health"])
async def health_redis() -> JSONResponse:
    """Check Redis connectivity."""
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        pong = await r.ping()
        await r.close()
        return JSONResponse({"status": "ok", "redis": "connected", "ping": pong})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "redis": str(exc)},
            status_code=503,
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
