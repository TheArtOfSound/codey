from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from codey.saas.billing.stripe_setup import setup_stripe_products
from codey.saas.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await setup_stripe_products()
    yield


app = FastAPI(title="Codey API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(session_router)
app.include_router(repo_router)
app.include_router(billing_router)
app.include_router(credit_router)
app.include_router(admin_router)
app.include_router(referral_router)
