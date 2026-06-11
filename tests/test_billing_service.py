from __future__ import annotations

import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

import codey.saas.billing.service as billing_service
from codey.saas.billing.plans import PLANS
from codey.saas.config import settings


class _FakeDB:
    def __init__(self) -> None:
        self.flush_calls = 0

    async def flush(self) -> None:
        self.flush_calls += 1


class _FailingMetadata:
    def to_dict_recursive(self):
        raise RuntimeError("metadata unavailable")


class _OverflowingTimestamp:
    def __float__(self) -> float:
        raise OverflowError("timestamp too large")


def test_module_initialization_rejects_whitespace_stripe_secret_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "   ")

    reloaded = importlib.reload(billing_service)

    assert reloaded.stripe.api_key == ""


def test_module_initialization_rejects_control_character_stripe_secret_key(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_live_\ninvalid")

    reloaded = importlib.reload(billing_service)

    assert reloaded.stripe.api_key == ""


def test_module_initialization_rejects_internal_whitespace_stripe_secret_key(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_live invalid")

    reloaded = importlib.reload(billing_service)

    assert reloaded.stripe.api_key == ""


def test_stripe_timestamp_helpers_reject_float_conversion_overflow() -> None:
    timestamp = _OverflowingTimestamp()

    assert billing_service._stripe_timestamp_to_datetime(timestamp) is None
    assert billing_service._serialize_stripe_timestamp(timestamp) == ""


@pytest.mark.asyncio
async def test_confirm_subscription_rejects_non_string_plan_metadata(monkeypatch) -> None:
    db = _FakeDB()
    service = billing_service.BillingService(db)
    user = SimpleNamespace(
        id=uuid4(),
        plan="free",
        plan_status="active",
        subscription_id=None,
        subscription_period_end=None,
        credits_remaining=0,
        credits_used_this_month=0,
    )

    async def fake_get_user(user_id, *, lock=False):
        assert lock is True
        return user

    monkeypatch.setattr(service, "_get_user", fake_get_user)
    monkeypatch.setattr(
        billing_service.stripe.Subscription,
        "retrieve",
        lambda subscription_id: SimpleNamespace(
            status="active",
            metadata={"codey_plan": ["pro"]},
        ),
    )

    with pytest.raises(billing_service.BillingError, match="missing codey_plan metadata"):
        await service.confirm_subscription(uuid4(), "sub_123")

    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_confirm_subscription_rejects_unreadable_plan_metadata(monkeypatch) -> None:
    db = _FakeDB()
    service = billing_service.BillingService(db)
    user = SimpleNamespace(
        id=uuid4(),
        plan="free",
        plan_status="active",
        subscription_id=None,
        subscription_period_end=None,
        credits_remaining=0,
        credits_used_this_month=0,
    )

    async def fake_get_user(user_id, *, lock=False):
        assert lock is True
        return user

    monkeypatch.setattr(service, "_get_user", fake_get_user)
    monkeypatch.setattr(
        billing_service.stripe.Subscription,
        "retrieve",
        lambda subscription_id: SimpleNamespace(
            status="active",
            metadata=_FailingMetadata(),
        ),
    )

    with pytest.raises(billing_service.BillingError, match="missing codey_plan metadata"):
        await service.confirm_subscription(uuid4(), "sub_123")

    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_change_plan_tolerates_legacy_plan_and_credit_fields(monkeypatch) -> None:
    db = _FakeDB()
    service = billing_service.BillingService(db)
    user = SimpleNamespace(
        id=uuid4(),
        plan=["starter"],
        plan_status="active",
        subscription_id="sub_123",
        subscription_period_end="stale",
        credits_remaining=" 100 ",
    )

    async def fake_get_user(user_id, *, lock=False):
        assert lock is True
        return user

    class _FakeSubscription:
        status = "active"

        def __getitem__(self, key):
            if key == "items":
                return {"data": [SimpleNamespace(id="si_123")]}
            raise KeyError(key)

    async def fake_ensure_catalog_loaded():
        return None

    monkeypatch.setattr(service, "_get_user", fake_get_user)
    monkeypatch.setattr(
        billing_service,
        "ensure_stripe_catalog_loaded",
        fake_ensure_catalog_loaded,
    )
    monkeypatch.setattr(
        billing_service.stripe.Subscription,
        "retrieve",
        lambda subscription_id: _FakeSubscription(),
    )
    monkeypatch.setattr(
        billing_service.stripe.Subscription,
        "modify",
        lambda subscription_id, **kwargs: None,
    )

    result = await service.change_plan(user.id, "pro")

    assert user.plan == "pro"
    assert user.plan_status == "active"
    assert user.subscription_period_end is None
    assert user.credits_remaining == 100
    assert result == {
        "old_plan": "",
        "new_plan": "pro",
        "credits": 100,
        "subscription_id": "sub_123",
    }
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_create_subscription_rejects_whitespace_price_id_before_lookup(
    monkeypatch,
) -> None:
    db = _FakeDB()
    service = billing_service.BillingService(db)

    async def fake_ensure_catalog_loaded():
        return None

    async def fake_get_user(user_id, *, lock=False):
        raise AssertionError("user lookup should not run when price_id is blank")

    monkeypatch.setattr(
        billing_service,
        "ensure_stripe_catalog_loaded",
        fake_ensure_catalog_loaded,
    )
    monkeypatch.setattr(service, "_get_user", fake_get_user)
    monkeypatch.setitem(PLANS["pro"], "stripe_price_id", "   ")

    with pytest.raises(
        billing_service.BillingError,
        match="Stripe price not configured for plan 'pro'",
    ):
        await service.create_subscription(uuid4(), "pro")


@pytest.mark.asyncio
async def test_create_subscription_rejects_control_character_price_id_before_lookup(
    monkeypatch,
) -> None:
    db = _FakeDB()
    service = billing_service.BillingService(db)

    async def fake_ensure_catalog_loaded():
        return None

    async def fake_get_user(user_id, *, lock=False):
        raise AssertionError("user lookup should not run when price_id is invalid")

    monkeypatch.setattr(
        billing_service,
        "ensure_stripe_catalog_loaded",
        fake_ensure_catalog_loaded,
    )
    monkeypatch.setattr(service, "_get_user", fake_get_user)
    monkeypatch.setitem(PLANS["pro"], "stripe_price_id", "price_\tpro")

    with pytest.raises(
        billing_service.BillingError,
        match="Stripe price not configured for plan 'pro'",
    ):
        await service.create_subscription(uuid4(), "pro")


@pytest.mark.asyncio
async def test_create_subscription_rejects_internal_whitespace_price_id_before_lookup(
    monkeypatch,
) -> None:
    db = _FakeDB()
    service = billing_service.BillingService(db)

    async def fake_ensure_catalog_loaded():
        return None

    async def fake_get_user(user_id, *, lock=False):
        raise AssertionError("user lookup should not run when price_id is invalid")

    monkeypatch.setattr(
        billing_service,
        "ensure_stripe_catalog_loaded",
        fake_ensure_catalog_loaded,
    )
    monkeypatch.setattr(service, "_get_user", fake_get_user)
    monkeypatch.setitem(PLANS["pro"], "stripe_price_id", "price pro")

    with pytest.raises(
        billing_service.BillingError,
        match="Stripe price not configured for plan 'pro'",
    ):
        await service.create_subscription(uuid4(), "pro")


@pytest.mark.asyncio
async def test_change_plan_rejects_whitespace_price_id_before_lookup(monkeypatch) -> None:
    db = _FakeDB()
    service = billing_service.BillingService(db)

    async def fake_ensure_catalog_loaded():
        return None

    async def fake_get_user(user_id, *, lock=False):
        raise AssertionError("user lookup should not run when price_id is blank")

    monkeypatch.setattr(
        billing_service,
        "ensure_stripe_catalog_loaded",
        fake_ensure_catalog_loaded,
    )
    monkeypatch.setattr(service, "_get_user", fake_get_user)
    monkeypatch.setitem(PLANS["pro"], "stripe_price_id", "   ")

    with pytest.raises(
        billing_service.BillingError,
        match="Stripe price not configured for 'pro'",
    ):
        await service.change_plan(uuid4(), "pro")


@pytest.mark.asyncio
async def test_change_plan_rejects_control_character_price_id_before_lookup(
    monkeypatch,
) -> None:
    db = _FakeDB()
    service = billing_service.BillingService(db)

    async def fake_ensure_catalog_loaded():
        return None

    async def fake_get_user(user_id, *, lock=False):
        raise AssertionError("user lookup should not run when price_id is invalid")

    monkeypatch.setattr(
        billing_service,
        "ensure_stripe_catalog_loaded",
        fake_ensure_catalog_loaded,
    )
    monkeypatch.setattr(service, "_get_user", fake_get_user)
    monkeypatch.setitem(PLANS["pro"], "stripe_price_id", "price_\tpro")

    with pytest.raises(
        billing_service.BillingError,
        match="Stripe price not configured for 'pro'",
    ):
        await service.change_plan(uuid4(), "pro")


@pytest.mark.asyncio
async def test_cancel_subscription_ignores_invalid_period_end(monkeypatch) -> None:
    db = _FakeDB()
    service = billing_service.BillingService(db)
    user = SimpleNamespace(
        id=uuid4(),
        subscription_id="sub_123",
        plan_status="active",
        subscription_period_end="stale",
    )

    async def fake_get_user(user_id, *, lock=False):
        assert lock is True
        return user

    monkeypatch.setattr(service, "_get_user", fake_get_user)
    monkeypatch.setattr(
        billing_service.stripe.Subscription,
        "modify",
        lambda subscription_id, **kwargs: SimpleNamespace(current_period_end={"seconds": 123}),
    )

    result = await service.cancel_subscription(user.id)

    assert user.plan_status == "cancelling"
    assert user.subscription_period_end is None
    assert result == {
        "status": "cancelling",
        "access_until": "",
        "subscription_id": "sub_123",
    }
    assert db.flush_calls == 1


def test_require_customer_rejects_whitespace_customer_id() -> None:
    service = billing_service.BillingService(_FakeDB())
    user = SimpleNamespace(id=uuid4(), stripe_customer_id="   ")

    with pytest.raises(
        billing_service.BillingError,
        match="has no Stripe customer",
    ):
        service._require_customer(user)


def test_require_customer_rejects_control_character_customer_id() -> None:
    service = billing_service.BillingService(_FakeDB())
    user = SimpleNamespace(id=uuid4(), stripe_customer_id="cus_\t123")

    with pytest.raises(
        billing_service.BillingError,
        match="has no Stripe customer",
    ):
        service._require_customer(user)
