from __future__ import annotations

from types import SimpleNamespace

import pytest

import codey.saas.api.user_routes as user_routes


@pytest.mark.asyncio
async def test_get_my_credits_normalizes_malformed_fields(monkeypatch) -> None:
    async def fake_get_balance(self, user_id):
        return {
            "subscription_credits": " 100 ",
            "topup_credits": {"credits": 25},
            "total": 150.0,
            "used_this_month": ["10"],
            "plan": {"plan": "pro"},
            "monthly_allocation": " 500 ",
        }

    monkeypatch.setattr(
        user_routes.CreditService,
        "get_balance",
        fake_get_balance,
    )

    response = await user_routes.get_my_credits(
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert response.subscription_credits == 100
    assert response.topup_credits == 0
    assert response.total == 150
    assert response.used_this_month == 0
    assert response.plan == ""
    assert response.monthly_allocation == 500
