from __future__ import annotations

from types import SimpleNamespace

import pytest

import codey.saas.api.credit_routes as credit_routes


@pytest.mark.asyncio
async def test_get_balance_normalizes_malformed_fields(monkeypatch) -> None:
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
        credit_routes.CreditService,
        "get_balance",
        fake_get_balance,
    )

    response = await credit_routes.get_balance(
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert response.subscription_credits == 100
    assert response.topup_credits == 0
    assert response.total == 150
    assert response.used_this_month == 0
    assert response.plan == ""
    assert response.monthly_allocation == 500


def test_credit_int_coercion_rejects_non_finite_values() -> None:
    assert credit_routes._coerce_credit_int(float("nan"), fallback=-1) == -1
    assert credit_routes._coerce_credit_int(float("inf"), fallback=-1) == -1
    assert credit_routes._coerce_credit_int("-inf", fallback=-1) == -1
    assert credit_routes._coerce_credit_int("3", fallback=-1) == 3
    assert credit_routes._coerce_optional_credit_int(float("nan")) == 0
    assert credit_routes._coerce_optional_credit_int("inf") == 0
    assert credit_routes._coerce_optional_credit_int("123") == 123


@pytest.mark.asyncio
async def test_get_history_uses_query_defaults_when_called_directly(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_transaction_history(self, user_id, *, limit: int = 50, offset: int = 0):
        captured["user_id"] = user_id
        captured["limit"] = limit
        captured["offset"] = offset
        return []

    monkeypatch.setattr(
        credit_routes.CreditService,
        "get_transaction_history",
        fake_get_transaction_history,
    )

    response = await credit_routes.get_history(
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert captured == {"user_id": "user-1", "limit": 50, "offset": 0}
    assert response.limit == 50
    assert response.offset == 0
    assert response.transactions == []


@pytest.mark.asyncio
async def test_get_history_normalizes_malformed_transactions(monkeypatch) -> None:
    async def fake_get_transaction_history(self, user_id, *, limit: int = 50, offset: int = 0):
        return [
            {
                "id": ["tx-1"],
                "amount": " 25 ",
                "type": {"type": "usage"},
                "description": ["Used credits"],
                "credits_before": " 100 ",
                "credits_after": {"value": 75},
                "session_id": ["session-1"],
                "created_at": {"created_at": "2026-01-01T00:00:00Z"},
            }
        ]

    monkeypatch.setattr(
        credit_routes.CreditService,
        "get_transaction_history",
        fake_get_transaction_history,
    )

    response = await credit_routes.get_history(
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert len(response.transactions) == 1
    assert response.transactions[0].id == ""
    assert response.transactions[0].amount == 25
    assert response.transactions[0].type == ""
    assert response.transactions[0].description is None
    assert response.transactions[0].credits_before == 100
    assert response.transactions[0].credits_after == 0
    assert response.transactions[0].session_id is None
    assert response.transactions[0].created_at == ""


@pytest.mark.asyncio
async def test_estimate_cost_uses_query_default_mode_when_called_directly() -> None:
    response = await credit_routes.estimate_cost(prompt="print('hi')")

    assert response.mode == "prompt"
    assert response.prompt_length == len("print('hi')")
    assert response.estimated_credits == 1
