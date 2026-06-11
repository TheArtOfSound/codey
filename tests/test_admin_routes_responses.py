from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import codey.saas.api.admin_routes as admin_routes


def test_user_to_search_result_tolerates_string_timestamps() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="Repo User",
        plan="pro",
        credits_remaining=12,
        topup_credits=4,
        created_at=" 2026-01-02T03:04:05Z ",
        last_active="2026-01-03T03:04:05Z",
    )

    response = admin_routes._user_to_search_result(user)

    assert response.created_at == "2026-01-02T03:04:05Z"
    assert response.last_active == "2026-01-03T03:04:05Z"


def test_user_to_search_result_coerces_malformed_profile_fields() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name=["Repo User"],
        plan=["pro"],
        credits_remaining="12",
        topup_credits={"value": 4},
        created_at="2026-01-02T03:04:05Z",
        last_active=None,
    )

    response = admin_routes._user_to_search_result(user)

    assert response.name is None
    assert response.plan == "free"
    assert response.credits_remaining == 12
    assert response.topup_credits == 0


def test_user_to_search_result_coerces_malformed_email() -> None:
    user = SimpleNamespace(
        id="user-1",
        email=["user@example.com"],
        name="Repo User",
        plan="pro",
        credits_remaining=12,
        topup_credits=4,
        created_at="2026-01-02T03:04:05Z",
        last_active=None,
    )

    response = admin_routes._user_to_search_result(user)

    assert response.email == ""


def test_user_to_search_result_rejects_control_character_text_fields() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user\n@example.com",
        name="Repo\tUser",
        plan="p\x7fro",
        credits_remaining=12,
        topup_credits=4,
        created_at="2026-01-02T03:04:05Z",
        last_active=None,
    )

    response = admin_routes._user_to_search_result(user)

    assert response.email == ""
    assert response.name is None
    assert response.plan == "free"


def test_user_to_search_result_tolerates_missing_legacy_fields() -> None:
    response = admin_routes._user_to_search_result(SimpleNamespace(id="user-2"))

    assert response.id == "user-2"
    assert response.email == ""
    assert response.name is None
    assert response.plan == "free"
    assert response.credits_remaining == 0
    assert response.topup_credits == 0
    assert response.created_at == ""
    assert response.last_active is None


def test_credit_adjustment_to_response_coerces_malformed_fields() -> None:
    user = SimpleNamespace(
        credits_remaining="12",
        topup_credits={"value": 4},
    )

    response = admin_routes._credit_adjustment_to_response(
        uuid.uuid4(),
        user,
        " 7 ",
        ["grant"],
    )

    assert response.new_credits_remaining == 12
    assert response.new_topup_credits == 0
    assert response.adjustment == 7
    assert response.reason == ""


def test_credit_adjustment_to_response_tolerates_missing_legacy_fields() -> None:
    response = admin_routes._credit_adjustment_to_response(
        "user-1",
        SimpleNamespace(),
        5,
        "grant",
    )

    assert response.user_id == "user-1"
    assert response.new_credits_remaining == 0
    assert response.new_topup_credits == 0
    assert response.adjustment == 5
    assert response.reason == "grant"


def test_announcement_to_response_coerces_malformed_fields() -> None:
    response = admin_routes._announcement_to_response(
        {
            "message": ["scheduled maintenance"],
            "level": {"level": "warning"},
        }
    )

    assert response.message is None
    assert response.level == "info"


def test_announcement_to_response_rejects_control_character_text_fields() -> None:
    response = admin_routes._announcement_to_response(
        {
            "message": "scheduled\nmaintenance",
            "level": "warn\ting",
        }
    )

    assert response.message is None
    assert response.level == "info"


def test_plan_monthly_usd_matches_billing_plan_config() -> None:
    expected = {
        plan: float(details["price_monthly"]) / 100.0
        for plan, details in admin_routes.PLANS.items()
    }

    assert {plan: admin_routes._PLAN_MONTHLY_USD[plan] for plan in expected} == expected
    assert admin_routes._PLAN_MONTHLY_USD["enterprise"] == 199.0


def test_plan_breakdown_accepts_indexable_row_like_values() -> None:
    class _RowLike:
        def __getitem__(self, index: int):
            return (" team ", "3")[index]

    response = admin_routes._plan_breakdown_to_response(_RowLike())

    assert response.plan == "team"
    assert response.count == 3


def test_stats_to_response_coerces_malformed_aggregate_fields() -> None:
    response = admin_routes._stats_to_response(
        total_users="10",
        users_by_plan=[
            (" pro ", "2"),
            (["team"], {"count": 3}),
        ],
        total_credits_used="40",
        total_api_cost="12.5",
        total_sessions=["9"],
        signups_last_30={"count": 1},
        paid_users="2",
    )

    assert response.total_users == 10
    assert [(plan.plan, plan.count) for plan in response.users_by_plan] == [
        ("pro", 2),
        ("free", 0),
    ]
    assert response.mrr_usd == 98.0
    assert response.total_credits_used == 40
    assert response.total_api_cost_usd == 12.5
    assert response.gross_margin == 87.24
    assert response.total_sessions == 0
    assert response.signups_last_30_days == 0
    assert response.conversion_rate == 20.0


def test_admin_numeric_coercion_rejects_non_finite_values() -> None:
    assert admin_routes._coerce_admin_int(float("nan"), fallback=-1) == -1
    assert admin_routes._coerce_admin_int(float("inf"), fallback=-1) == -1
    assert admin_routes._coerce_admin_int("-inf", fallback=-1) == -1
    assert admin_routes._coerce_admin_int("3", fallback=-1) == 3
    assert admin_routes._coerce_admin_float(float("nan"), fallback=-1.0) == -1.0
    assert admin_routes._coerce_admin_float("inf", fallback=-1.0) == -1.0
    assert admin_routes._coerce_admin_float("0.42", fallback=-1.0) == 0.42


def test_admin_row_list_coercion_rejects_malformed_results() -> None:
    row = SimpleNamespace(id="row-1")

    assert admin_routes._coerce_admin_row_list([row]) == [row]
    assert admin_routes._coerce_admin_row_list((row,)) == [row]
    assert admin_routes._coerce_admin_row_list(None) == []
    assert admin_routes._coerce_admin_row_list("bad") == []


class _AdjustCreditsDB:
    def __init__(self, user) -> None:
        self._user = user
        self.added: list[object] = []
        self.flush_calls = 0

    async def get(self, _model, _user_id):
        return self._user

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_calls += 1


@pytest.mark.asyncio
async def test_adjust_credits_coerces_legacy_topup_credit_fields() -> None:
    user = SimpleNamespace(
        credits_remaining="12",
        topup_credits="7",
    )
    db = _AdjustCreditsDB(user)

    response = await admin_routes.adjust_credits(
        uuid.uuid4(),
        admin_routes.CreditAdjustmentRequest(amount=-10, reason="revoke"),
        _admin=SimpleNamespace(id="admin-1"),
        db=db,
    )

    assert user.topup_credits == 0
    assert len(db.added) == 1
    assert db.added[0].credits_before == 7
    assert db.added[0].credits_after == 0
    assert db.flush_calls == 1
    assert response.new_credits_remaining == 12
    assert response.new_topup_credits == 0


@pytest.mark.asyncio
async def test_adjust_credits_tolerates_missing_legacy_topup_field() -> None:
    user = SimpleNamespace(credits_remaining=12)
    db = _AdjustCreditsDB(user)

    response = await admin_routes.adjust_credits(
        uuid.uuid4(),
        admin_routes.CreditAdjustmentRequest(amount=5, reason="grant"),
        _admin=SimpleNamespace(id="admin-1"),
        db=db,
    )

    assert user.topup_credits == 5
    assert len(db.added) == 1
    assert db.added[0].credits_before == 0
    assert db.added[0].credits_after == 5
    assert response.new_topup_credits == 5
