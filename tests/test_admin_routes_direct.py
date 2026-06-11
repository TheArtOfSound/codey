from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import codey.saas.api.admin_routes as admin_routes


class _UsersResult:
    def __init__(self, users) -> None:
        self._users = users

    def scalars(self):
        return self

    def all(self):
        return self._users


class _AdminUsersDB:
    def __init__(self, users) -> None:
        self._users = users

    async def execute(self, _statement):
        return _UsersResult(self._users)


@pytest.mark.asyncio
async def test_search_users_uses_query_default_limit_when_called_directly() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="Repo User",
        plan="pro",
        credits_remaining=12,
        topup_credits=4,
        created_at="2026-01-02T03:04:05Z",
        last_active=None,
    )

    response = await admin_routes.search_users(
        search="user@example.com",
        _admin=SimpleNamespace(id="admin-1", plan="enterprise"),
        db=_AdminUsersDB([user]),
    )

    assert len(response) == 1
    assert response[0].email == "user@example.com"
    assert response[0].created_at == "2026-01-02T03:04:05Z"


@pytest.mark.asyncio
async def test_require_admin_allows_normalized_enterprise_plan() -> None:
    current_user = SimpleNamespace(plan=" Enterprise ")

    allowed_user = await admin_routes.require_admin(current_user=current_user)

    assert allowed_user is current_user


@pytest.mark.asyncio
async def test_require_admin_allows_explicit_admin_flag() -> None:
    current_user = SimpleNamespace(plan={"name": "free"}, is_admin=True)

    allowed_user = await admin_routes.require_admin(current_user=current_user)

    assert allowed_user is current_user


@pytest.mark.asyncio
async def test_require_admin_fails_closed_for_malformed_plan() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await admin_routes.require_admin(
            current_user=SimpleNamespace(plan={"name": "enterprise"}),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Admin access required"
