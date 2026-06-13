"""Basic smoke tests for API health endpoints."""
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException, status
from httpx import AsyncClient, ASGITransport

from codey.saas.api.app import app
from codey.saas.api.auth_routes import AuthService
from codey.saas.auth.public_urls import FRONTEND_ORIGIN_HEADER
from codey.saas.database import get_db


class _NoDatabaseSession:
    def add(self, _obj) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("Smoke test unexpectedly touched the real database")

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _smoke_user(email: str, name: str = "Test User") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        email=email,
        name=name,
        avatar_url=None,
        github_id=None,
        github_token=None,
        plan="free",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        created_at=datetime.now(timezone.utc),
    )


@pytest_asyncio.fixture
async def client():
    async def _fake_get_db():
        yield _NoDatabaseSession()

    app.dependency_overrides[get_db] = _fake_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_billing_plans(client):
    resp = await client.get("/billing/plans")
    assert resp.status_code == 200
    data = resp.json()
    assert "plans" in data
    plans = data["plans"]
    assert len(plans) >= 4
    plan_keys = [p["key"] for p in plans]
    assert "free" in plan_keys
    assert "starter" in plan_keys
    assert "pro" in plan_keys


@pytest.mark.asyncio
async def test_signup(client, monkeypatch):
    email = f"test_{uuid.uuid4().hex[:8]}@test.dev"

    async def fake_signup(self, *, email: str, password: str, name: str | None, frontend_origin: str):
        assert password == "TestPass1234"
        assert frontend_origin == "http://test"
        return _smoke_user(email=email, name=name or "Test User"), "test-token"

    monkeypatch.setattr(AuthService, "signup", fake_signup)

    resp = await client.post("/auth/signup", json={
        "email": email,
        "password": "TestPass1234",
        "name": "Test User",
    }, headers={FRONTEND_ORIGIN_HEADER: "http://test"})
    assert resp.status_code == 201
    data = resp.json()
    assert "token" in data
    assert data["user"]["email"] == email
    assert data["user"]["credits_remaining"] == 10


@pytest.mark.asyncio
async def test_login_invalid(client, monkeypatch):
    async def fake_login(self, *, email: str, password: str):
        assert email == "nonexistent@test.dev"
        assert password == "wrong"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    monkeypatch.setattr(AuthService, "login", fake_login)

    resp = await client.post("/auth/login", json={
        "email": "nonexistent@test.dev",
        "password": "wrong",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_access(client):
    resp = await client.get("/users/me")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_sessions_prompt_requires_auth(client):
    resp = await client.post("/sessions/prompt", json={"prompt": "test"})
    assert resp.status_code in (401, 403)
