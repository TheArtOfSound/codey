"""Basic smoke tests for API health endpoints."""
import pytest
from httpx import AsyncClient, ASGITransport

from codey.saas.api.app import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


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
async def test_signup(client):
    import uuid
    email = f"test_{uuid.uuid4().hex[:8]}@test.dev"
    resp = await client.post("/auth/signup", json={
        "email": email,
        "password": "TestPass1234",
        "name": "Test User",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "token" in data
    assert data["user"]["email"] == email
    assert data["user"]["credits_remaining"] == 10


@pytest.mark.asyncio
async def test_login_invalid(client):
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
