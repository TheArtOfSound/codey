from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

import codey.saas.auth.dependencies as auth_dependencies
from codey.saas.auth.cookies import SESSION_COOKIE_NAME
from codey.saas.auth.jwt import create_access_token
from codey.saas.auth.websockets import authenticate_websocket
from codey.saas.auth.dependencies import PLAN_LEVELS, PLANS, require_plan


class _ScalarResult:
    def __init__(self, user) -> None:
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _FakeDB:
    def __init__(self, user) -> None:
        self._user = user

    async def execute(self, stmt):
        return _ScalarResult(self._user)


def test_plan_levels_match_billing_plan_config() -> None:
    expected = {
        plan: index
        for index, (plan, _details) in enumerate(
            sorted(
                PLANS.items(),
                key=lambda item: (float(item[1]["price_monthly"]), item[0]),
            )
        )
    }

    assert {plan: PLAN_LEVELS[plan] for plan in expected} == expected
    assert PLAN_LEVELS["enterprise"] == max(expected.values()) + 1


def test_coerce_plan_price_rejects_non_finite_values() -> None:
    assert auth_dependencies._coerce_plan_price("nan") == 0.0
    assert auth_dependencies._coerce_plan_price("inf") == 0.0
    assert auth_dependencies._coerce_plan_price("-inf") == 0.0
    assert auth_dependencies._coerce_plan_price(10**10000) == 0.0
    assert auth_dependencies._coerce_plan_price("19.99") == 19.99


def test_plan_display_name_falls_back_for_malformed_values() -> None:
    assert auth_dependencies._plan_display_name(" Team ", "pro") == "Team"
    assert auth_dependencies._plan_display_name(["Team"], "pro") == "Pro"
    assert auth_dependencies._plan_display_name(None, None) == "Free"


def test_require_plan_rejects_unknown_minimum_plan() -> None:
    with pytest.raises(ValueError, match="Unknown subscription plan: typo"):
        require_plan(" typo ")


def test_user_context_id_accepts_uuid_and_rejects_malformed_values() -> None:
    user_id = uuid.uuid4()

    assert auth_dependencies._coerce_user_context_id(user_id) == str(user_id)
    assert auth_dependencies._coerce_user_context_id(" user-1 ") == "user-1"
    assert auth_dependencies._coerce_user_context_id(None) is None
    assert auth_dependencies._coerce_user_context_id(["user-1"]) is None


@pytest.mark.asyncio
async def test_require_plan_normalizes_and_allows_higher_tiers() -> None:
    checker = require_plan("pro")

    current_user = SimpleNamespace(
        plan=" Team ",
        plan_display_name="Team",
    )

    allowed_user = await checker(current_user=current_user)

    assert allowed_user is current_user


@pytest.mark.asyncio
async def test_require_plan_fails_closed_for_malformed_plan() -> None:
    checker = require_plan("pro")

    current_user = SimpleNamespace(
        plan={"name": "pro"},
        plan_display_name="Starter",
    )

    with pytest.raises(HTTPException) as exc_info:
        await checker(current_user=current_user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == (
        "This feature requires the Pro plan or above. "
        "Your current plan is Starter."
    )


@pytest.mark.asyncio
async def test_require_plan_fails_closed_for_missing_legacy_plan_fields() -> None:
    checker = require_plan("pro")

    with pytest.raises(HTTPException) as exc_info:
        await checker(current_user=SimpleNamespace())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == (
        "This feature requires the Pro plan or above. "
        "Your current plan is Free."
    )


@pytest.mark.asyncio
async def test_get_current_user_accepts_whitespace_padded_cookie_token(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1")
    db = _FakeDB(user)
    token = create_access_token("user-1")
    request = SimpleNamespace(cookies={SESSION_COOKIE_NAME: f"  {token}  "})
    captured: dict[str, object] = {}

    async def fake_set_db_user_context(db_session, user_id: str) -> None:
        captured["db"] = db_session
        captured["user_id"] = user_id

    monkeypatch.setattr(auth_dependencies, "set_db_user_context", fake_set_db_user_context)

    current_user = await auth_dependencies.get_current_user(request, token=None, db=db)

    assert current_user is user
    assert captured == {"db": db, "user_id": "user-1"}


@pytest.mark.asyncio
async def test_get_current_user_falls_back_to_cookie_when_bearer_token_is_whitespace(
    monkeypatch,
) -> None:
    user = SimpleNamespace(id="user-1")
    db = _FakeDB(user)
    token = create_access_token("user-1")
    request = SimpleNamespace(cookies={SESSION_COOKIE_NAME: token})
    captured: dict[str, object] = {}

    async def fake_set_db_user_context(db_session, user_id: str) -> None:
        captured["db"] = db_session
        captured["user_id"] = user_id

    monkeypatch.setattr(auth_dependencies, "set_db_user_context", fake_set_db_user_context)

    current_user = await auth_dependencies.get_current_user(request, token="   ", db=db)

    assert current_user is user
    assert captured == {"db": db, "user_id": "user-1"}


@pytest.mark.asyncio
async def test_get_current_user_normalizes_token_subject_before_lookup(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1")
    db = _FakeDB(user)
    request = SimpleNamespace(cookies={})
    captured: dict[str, object] = {}

    class FakeUserIdColumn:
        def __eq__(self, other):
            captured["lookup_user_id"] = other
            return ("user_id_eq", other)

    fake_user_model = SimpleNamespace(id=FakeUserIdColumn())

    class FakeSelect:
        def where(self, condition):
            captured["where_condition"] = condition
            return self

    def fake_select(model):
        captured["selected_model"] = model
        return FakeSelect()

    async def fake_set_db_user_context(db_session, user_id: str) -> None:
        captured["db"] = db_session
        captured["context_user_id"] = user_id

    monkeypatch.setattr(auth_dependencies, "decode_access_token", lambda token: {"sub": " user-1 "})
    monkeypatch.setattr(auth_dependencies, "select", fake_select)
    monkeypatch.setattr(auth_dependencies, "set_db_user_context", fake_set_db_user_context)
    monkeypatch.setattr(auth_dependencies, "User", fake_user_model)

    current_user = await auth_dependencies.get_current_user(request, token="access-token", db=db)

    assert current_user is user
    assert captured["selected_model"] is fake_user_model
    assert captured["lookup_user_id"] == "user-1"
    assert captured["where_condition"] == ("user_id_eq", "user-1")
    assert captured["context_user_id"] == "user-1"


@pytest.mark.asyncio
async def test_get_current_user_rejects_malformed_db_user_id(monkeypatch) -> None:
    user = SimpleNamespace(id=None)
    db = _FakeDB(user)
    token = create_access_token("user-1")
    request = SimpleNamespace(cookies={})

    async def fail_set_db_user_context(*args, **kwargs) -> None:
        raise AssertionError("malformed user ids should not set database context")

    monkeypatch.setattr(auth_dependencies, "set_db_user_context", fail_set_db_user_context)

    with pytest.raises(HTTPException) as exc_info:
        await auth_dependencies.get_current_user(request, token=token, db=db)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "User not found"


def test_authenticate_websocket_accepts_whitespace_padded_cookie_token() -> None:
    token = create_access_token("user-1")
    websocket = SimpleNamespace(
        query_params={},
        cookies={SESSION_COOKIE_NAME: f"  {token}  "},
    )

    payload = authenticate_websocket(websocket)

    assert payload is not None
    assert payload["sub"] == "user-1"


def test_authenticate_websocket_falls_back_to_cookie_when_query_token_is_whitespace() -> None:
    token = create_access_token("user-1")
    websocket = SimpleNamespace(
        query_params={"token": "   "},
        cookies={SESSION_COOKIE_NAME: token},
    )

    payload = authenticate_websocket(websocket)

    assert payload is not None
    assert payload["sub"] == "user-1"
