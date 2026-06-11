from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import codey.saas.api.user_routes as user_routes
from codey.saas.billing.service import BillingError


class _FakeDB:
    def __init__(self) -> None:
        self.executed = 0
        self.flushed = 0

    async def execute(self, _statement) -> None:
        self.executed += 1

    async def flush(self) -> None:
        self.flushed += 1


def test_delete_me_continues_for_expected_billing_errors(monkeypatch) -> None:
    async def fake_cancel_subscription(self, _user_id):
        raise BillingError("already cancelled")

    monkeypatch.setattr(
        user_routes.BillingService,
        "cancel_subscription",
        fake_cancel_subscription,
    )

    db = _FakeDB()
    current_user = SimpleNamespace(id="user-1", subscription_id="sub_123")

    asyncio.run(
        user_routes.delete_me(
            user_routes.DeleteUserRequest(confirm="DELETE"),
            current_user=current_user,
            db=db,
        )
    )

    assert db.executed > 0
    assert db.flushed == 1


def test_delete_me_aborts_on_unexpected_subscription_cancel_failures(monkeypatch) -> None:
    async def fake_cancel_subscription(self, _user_id):
        raise RuntimeError("stripe unavailable")

    monkeypatch.setattr(
        user_routes.BillingService,
        "cancel_subscription",
        fake_cancel_subscription,
    )

    db = _FakeDB()
    current_user = SimpleNamespace(id="user-1", subscription_id="sub_123")

    with pytest.raises(RuntimeError, match="stripe unavailable"):
        asyncio.run(
            user_routes.delete_me(
                user_routes.DeleteUserRequest(confirm="DELETE"),
                current_user=current_user,
                db=db,
            )
        )

    assert db.executed == 0
    assert db.flushed == 0
