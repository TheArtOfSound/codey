from __future__ import annotations

import importlib
import logging
import sys
import types
from types import SimpleNamespace

import pytest

import codey.saas.billing.webhooks as billing_webhooks
from codey.saas.config import settings


class _FakeDB:
    def __init__(self) -> None:
        self.flush_calls = 0

    async def flush(self) -> None:
        self.flush_calls += 1


class _FailingExecuteDB:
    async def execute(self, _statement):
        raise AssertionError("db.execute should not be called for invalid customer ids")


class _StripeObjectMetadata:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_dict_recursive(self) -> dict[str, object]:
        return self._payload


class _OverflowingTimestamp:
    def __float__(self) -> float:
        raise OverflowError("timestamp too large")


def test_module_initialization_rejects_whitespace_stripe_secret_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "   ")

    reloaded = importlib.reload(billing_webhooks)

    assert reloaded.stripe.api_key == ""


def test_module_initialization_rejects_control_character_stripe_secret_key(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_live_\ninvalid")

    reloaded = importlib.reload(billing_webhooks)

    assert reloaded.stripe.api_key == ""


def test_module_initialization_rejects_internal_whitespace_stripe_secret_key(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_live invalid")

    reloaded = importlib.reload(billing_webhooks)

    assert reloaded.stripe.api_key == ""


def test_coerce_stripe_timestamp_rejects_float_conversion_overflow() -> None:
    assert billing_webhooks._coerce_stripe_timestamp(_OverflowingTimestamp()) is None


@pytest.mark.asyncio
async def test_handle_payment_intent_succeeded_ignores_non_dict_metadata(monkeypatch) -> None:
    db = _FakeDB()

    class _FailingCreditService:
        def __init__(self, _db) -> None:
            raise AssertionError("CreditService should not be constructed for invalid metadata")

    monkeypatch.setattr(billing_webhooks, "CreditService", _FailingCreditService)

    result = await billing_webhooks._handle_payment_intent_succeeded(
        {"id": "pi_123", "metadata": []},
        db,
    )

    assert result == {
        "status": "ok",
        "event": "payment_intent.succeeded",
        "action": "not_a_topup",
    }
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_handle_payment_intent_succeeded_accepts_stripe_object_metadata(
    monkeypatch,
) -> None:
    db = _FakeDB()
    captured: dict[str, object] = {}

    class _FakeCreditService:
        def __init__(self, db_session) -> None:
            captured["db"] = db_session

        async def add_topup_credits(
            self,
            *,
            user_id,
            amount: int,
            stripe_payment_intent_id: str,
        ) -> None:
            captured["user_id"] = str(user_id)
            captured["amount"] = amount
            captured["stripe_payment_intent_id"] = stripe_payment_intent_id

    monkeypatch.setattr(billing_webhooks, "CreditService", _FakeCreditService)

    result = await billing_webhooks._handle_payment_intent_succeeded(
        {
            "id": "pi_123",
            "metadata": _StripeObjectMetadata(
                {
                    "type": " codey_topup ",
                    "user_id": "f6a61288-aebe-4e98-b24b-a5774f74ec9f",
                    "package": "starter_pack",
                    "credits": "50",
                }
            ),
        },
        db,
    )

    assert result == {
        "status": "ok",
        "event": "payment_intent.succeeded",
        "action": "topup_credits_added",
        "credits": 50,
    }
    assert captured == {
        "db": db,
        "user_id": "f6a61288-aebe-4e98-b24b-a5774f74ec9f",
        "amount": 50,
        "stripe_payment_intent_id": "pi_123",
    }
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_handle_payment_intent_succeeded_rejects_missing_payment_intent_id(
    monkeypatch,
) -> None:
    db = _FakeDB()

    class _FailingCreditService:
        def __init__(self, _db) -> None:
            raise AssertionError(
                "CreditService should not be constructed without a payment intent id"
            )

    monkeypatch.setattr(billing_webhooks, "CreditService", _FailingCreditService)

    result = await billing_webhooks._handle_payment_intent_succeeded(
        {
            "id": "   ",
            "metadata": {
                "type": "codey_topup",
                "user_id": "f6a61288-aebe-4e98-b24b-a5774f74ec9f",
                "package": "starter_pack",
                "credits": "50",
            },
        },
        db,
    )

    assert result == {"status": "error", "reason": "missing_payment_intent_id"}
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_handle_stripe_webhook_trims_whitespace_webhook_secret(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_construct_event(payload, sig_header, secret):
        captured["payload"] = payload
        captured["sig_header"] = sig_header
        captured["secret"] = secret
        return {"id": "evt_123", "type": "unknown.event", "data": {"object": {}}}

    monkeypatch.setattr(settings, "stripe_webhook_secret", " whsec_123 ")
    monkeypatch.setattr(
        billing_webhooks.stripe.Webhook,
        "construct_event",
        staticmethod(fake_construct_event),
    )

    result = await billing_webhooks.handle_stripe_webhook(b"payload", "sig", _FakeDB())

    assert result == {"status": "ignored", "event": "unknown.event"}
    assert captured == {
        "payload": b"payload",
        "sig_header": "sig",
        "secret": "whsec_123",
    }


@pytest.mark.asyncio
async def test_handle_stripe_webhook_rejects_control_character_webhook_secret(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_construct_event(payload, sig_header, secret):
        captured["payload"] = payload
        captured["sig_header"] = sig_header
        captured["secret"] = secret
        return {"id": "evt_123", "type": "unknown.event", "data": {"object": {}}}

    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_\tbad")
    monkeypatch.setattr(
        billing_webhooks.stripe.Webhook,
        "construct_event",
        staticmethod(fake_construct_event),
    )

    result = await billing_webhooks.handle_stripe_webhook(b"payload", "sig", _FakeDB())

    assert result == {"status": "ignored", "event": "unknown.event"}
    assert captured == {
        "payload": b"payload",
        "sig_header": "sig",
        "secret": "",
    }


@pytest.mark.asyncio
async def test_handle_stripe_webhook_rejects_internal_whitespace_webhook_secret(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_construct_event(payload, sig_header, secret):
        captured["payload"] = payload
        captured["sig_header"] = sig_header
        captured["secret"] = secret
        return {"id": "evt_123", "type": "unknown.event", "data": {"object": {}}}

    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec bad")
    monkeypatch.setattr(
        billing_webhooks.stripe.Webhook,
        "construct_event",
        staticmethod(fake_construct_event),
    )

    result = await billing_webhooks.handle_stripe_webhook(b"payload", "sig", _FakeDB())

    assert result == {"status": "ignored", "event": "unknown.event"}
    assert captured == {
        "payload": b"payload",
        "sig_header": "sig",
        "secret": "",
    }


@pytest.mark.asyncio
async def test_handle_payment_intent_succeeded_rejects_unknown_package_metadata(
    monkeypatch,
) -> None:
    db = _FakeDB()

    class _FailingCreditService:
        def __init__(self, _db) -> None:
            raise AssertionError("CreditService should not be constructed for invalid package metadata")

    monkeypatch.setattr(billing_webhooks, "CreditService", _FailingCreditService)

    result = await billing_webhooks._handle_payment_intent_succeeded(
        {
            "id": "pi_123",
            "metadata": {
                "type": "codey_topup",
                "user_id": "f6a61288-aebe-4e98-b24b-a5774f74ec9f",
                "package": "unknown",
                "credits": "50",
            },
        },
        db,
    )

    assert result == {"status": "error", "reason": "invalid_package_metadata"}
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_handle_payment_intent_succeeded_rejects_whitespace_metadata_values(
    monkeypatch,
) -> None:
    db = _FakeDB()

    class _FailingCreditService:
        def __init__(self, _db) -> None:
            raise AssertionError("CreditService should not be constructed for blank metadata")

    monkeypatch.setattr(billing_webhooks, "CreditService", _FailingCreditService)

    result = await billing_webhooks._handle_payment_intent_succeeded(
        {
            "id": "pi_123",
            "metadata": {
                "type": " codey_topup ",
                "user_id": "   ",
                "package": "small",
                "credits": "50",
            },
        },
        db,
    )

    assert result == {"status": "error", "reason": "incomplete_metadata"}
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_handle_payment_intent_succeeded_rejects_control_character_metadata_values(
    monkeypatch,
) -> None:
    db = _FakeDB()

    class _FailingCreditService:
        def __init__(self, _db) -> None:
            raise AssertionError("CreditService should not be constructed for bad metadata")

    monkeypatch.setattr(billing_webhooks, "CreditService", _FailingCreditService)

    result = await billing_webhooks._handle_payment_intent_succeeded(
        {
            "id": "pi_123",
            "metadata": {
                "type": "codey_topup",
                "user_id": "f6a61288-aebe-4e98-b24b-a5774f74ec\t9f",
                "package": "small",
                "credits": "50",
            },
        },
        db,
    )

    assert result == {"status": "error", "reason": "incomplete_metadata"}
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_handle_subscription_created_rejects_non_dict_metadata(monkeypatch) -> None:
    db = _FakeDB()
    user = SimpleNamespace(
        id="user-1",
        plan="free",
        plan_status="active",
        subscription_id=None,
        credits_remaining=0,
        credits_used_this_month=0,
        subscription_period_end=None,
    )

    async def fake_get_user_by_customer(customer_id, db_session, lock=False):
        assert customer_id == "cus_123"
        assert db_session is db
        assert lock is False
        return user

    monkeypatch.setattr(
        billing_webhooks,
        "_get_user_by_customer",
        fake_get_user_by_customer,
    )

    result = await billing_webhooks._handle_subscription_created(
        {"id": "sub_123", "customer": "cus_123", "status": "active", "metadata": []},
        db,
    )

    assert result == {"status": "error", "reason": "invalid_plan_metadata"}
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_get_user_by_customer_rejects_whitespace_customer_id() -> None:
    user = await billing_webhooks._get_user_by_customer("   ", _FailingExecuteDB())

    assert user is None


@pytest.mark.asyncio
async def test_get_user_by_customer_rejects_control_character_customer_id() -> None:
    user = await billing_webhooks._get_user_by_customer(
        "cus_\t123",
        _FailingExecuteDB(),
    )

    assert user is None


@pytest.mark.asyncio
async def test_handle_subscription_created_ignores_invalid_period_end(monkeypatch) -> None:
    db = _FakeDB()
    user = SimpleNamespace(
        id="user-1",
        plan="free",
        plan_status="active",
        subscription_id=None,
        credits_remaining=0,
        credits_used_this_month=0,
        subscription_period_end=None,
    )

    async def fake_get_user_by_customer(customer_id, db_session, lock=False):
        assert customer_id == "cus_123"
        assert db_session is db
        assert lock is False
        return user

    monkeypatch.setattr(
        billing_webhooks,
        "_get_user_by_customer",
        fake_get_user_by_customer,
    )

    result = await billing_webhooks._handle_subscription_created(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "metadata": {"codey_plan": "pro"},
            "current_period_end": {"seconds": 123},
        },
        db,
    )

    assert result == {"status": "ok", "event": "customer.subscription.created"}
    assert user.plan == "pro"
    assert user.subscription_id == "sub_123"
    assert user.subscription_period_end is None
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_handle_subscription_created_fails_closed_for_non_string_status(monkeypatch) -> None:
    db = _FakeDB()
    user = SimpleNamespace(
        id="user-1",
        plan="free",
        plan_status="active",
        subscription_id=None,
        credits_remaining=0,
        credits_used_this_month=0,
        subscription_period_end=None,
    )

    async def fake_get_user_by_customer(customer_id, db_session, lock=False):
        assert customer_id == "cus_123"
        assert db_session is db
        assert lock is False
        return user

    monkeypatch.setattr(
        billing_webhooks,
        "_get_user_by_customer",
        fake_get_user_by_customer,
    )

    result = await billing_webhooks._handle_subscription_created(
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": {"state": "active"},
            "metadata": {"codey_plan": "pro"},
        },
        db,
    )

    assert result == {"status": "ok", "event": "customer.subscription.created"}
    assert user.plan == "pro"
    assert user.plan_status == "incomplete"
    assert user.subscription_id == "sub_123"
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_handle_invoice_payment_failed_sends_best_effort_email(monkeypatch) -> None:
    db = _FakeDB()
    user = SimpleNamespace(
        id="user-1",
        email=" user@example.com ",
        plan_status="active",
    )
    captured: dict[str, object] = {}

    async def fake_get_user_by_customer(customer_id, db_session, lock=False):
        assert customer_id == "cus_123"
        assert db_session is db
        assert lock is True
        return user

    class _FakeEmailService:
        async def send_payment_failed(self, email: str) -> bool:
            captured["email"] = email
            return True

    monkeypatch.setattr(
        billing_webhooks,
        "_get_user_by_customer",
        fake_get_user_by_customer,
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.emails.service",
        types.SimpleNamespace(EmailService=_FakeEmailService),
    )

    result = await billing_webhooks._handle_invoice_payment_failed(
        {"id": "in_123", "customer": "cus_123"},
        db,
    )

    assert result == {"status": "ok", "event": "invoice.payment_failed"}
    assert user.plan_status == "past_due"
    assert captured == {"email": "user@example.com"}
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_handle_invoice_payment_failed_redacts_email_failure_logs(
    monkeypatch,
    caplog,
) -> None:
    db = _FakeDB()
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        plan_status="active",
    )

    async def fake_get_user_by_customer(customer_id, db_session, lock=False):
        assert customer_id == "cus_123"
        assert db_session is db
        assert lock is True
        return user

    class _FailingEmailService:
        async def send_payment_failed(self, email: str) -> bool:
            raise RuntimeError(
                f"SMTP rejected {email} via https://user:url-secret@mail.example.test/send "
                "access_token=access-secret auth_token=auth-secret "
                "refresh_token=refresh-secret client_secret=client-secret "
                "mirror=https://mail.example.test/send#client_secret=fragment-secret "
                "authorization=Bearer bearer-secret"
            )

    monkeypatch.setattr(
        billing_webhooks,
        "_get_user_by_customer",
        fake_get_user_by_customer,
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.emails.service",
        types.SimpleNamespace(EmailService=_FailingEmailService),
    )
    caplog.set_level(logging.WARNING, logger="codey.saas.billing.webhooks")

    result = await billing_webhooks._handle_invoice_payment_failed(
        {"id": "in_123", "customer": "cus_123"},
        db,
    )

    assert result == {"status": "ok", "event": "invoice.payment_failed"}
    assert user.plan_status == "past_due"
    assert db.flush_calls == 1
    assert "user@example.com" not in caplog.text
    assert "url-secret" not in caplog.text
    assert "access-secret" not in caplog.text
    assert "auth-secret" not in caplog.text
    assert "refresh-secret" not in caplog.text
    assert "client-secret" not in caplog.text
    assert "fragment-secret" not in caplog.text
    assert "bearer-secret" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@mail.example.test/send" in caplog.text
    assert "access_token=***" in caplog.text
    assert "auth_token=***" in caplog.text
    assert "refresh_token=***" in caplog.text
    assert "client_secret=***" in caplog.text
    assert "authorization=Bearer ***" in caplog.text
    assert "Traceback" not in caplog.text
