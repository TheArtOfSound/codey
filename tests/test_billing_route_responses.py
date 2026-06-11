from __future__ import annotations

from types import SimpleNamespace

import pytest

import codey.saas.api.billing_routes as billing_routes


def test_plan_to_response_normalizes_malformed_fields() -> None:
    response = billing_routes._plan_to_response(
        ["pro"],
        {
            "name": {"name": "Pro"},
            "price_monthly": " 4900 ",
            "credits": ["400"],
            "rollover": {"value": 200},
            "features": {
                "github_repos": " 5 ",
                "autonomous_mode": "yes",
                "priority": ["true"],
                "max_upload_mb": {"mb": 500},
                "seats": " 10 ",
            },
        },
    )

    assert response.key == ""
    assert response.name == "Plan"
    assert response.price_monthly == 4900
    assert response.credits == 0
    assert response.rollover == 0
    assert response.features.github_repos == 5
    assert response.features.autonomous_mode is True
    assert response.features.priority is False
    assert response.features.max_upload_mb == 0
    assert response.features.seats == 10


def test_billing_numeric_coercion_rejects_non_finite_values() -> None:
    assert billing_routes._coerce_billing_int(float("nan"), fallback=-1) == -1
    assert billing_routes._coerce_billing_int(float("inf"), fallback=-1) == -1
    assert billing_routes._coerce_billing_int("-inf", fallback=-1) == -1
    assert billing_routes._coerce_billing_int("3", fallback=-1) == 3
    assert billing_routes._coerce_billing_optional_int(float("nan")) is None
    assert billing_routes._coerce_billing_optional_int("inf") is None
    assert billing_routes._coerce_billing_optional_int("123") == 123
    assert billing_routes._coerce_billing_bool(float("nan"), fallback=False) is False
    assert billing_routes._coerce_billing_bool(float("inf"), fallback=False) is False
    assert billing_routes._coerce_billing_bool(1, fallback=False) is True


def test_webhook_to_response_normalizes_malformed_status() -> None:
    response = billing_routes._webhook_to_response(
        {
            "status": ["ok"],
        }
    )

    assert response.status == "ok"


def test_client_secret_responses_reject_internal_whitespace() -> None:
    subscribe = billing_routes._subscribe_to_response(
        {
            "client_secret": " pi_secret_123 ",
            "subscription_id": " sub_123 ",
            "type": " payment ",
        },
    )
    topup = billing_routes._topup_to_response({"client_secret": "pi secret 123"})
    add_payment = billing_routes._add_payment_method_to_response(
        {"client_secret": "seti secret 123"},
    )

    assert subscribe.client_secret == "pi_secret_123"
    assert subscribe.subscription_id == "sub_123"
    assert subscribe.type == "payment"
    assert topup.client_secret == ""
    assert add_payment.client_secret == ""


@pytest.mark.asyncio
async def test_subscribe_normalizes_malformed_fields(monkeypatch) -> None:
    async def fake_create_subscription(self, user_id, plan):
        return {
            "client_secret": {"secret": "pi_secret_123"},
            "subscription_id": ["sub_123"],
            "type": ["payment"],
        }

    monkeypatch.setattr(
        billing_routes.BillingService,
        "create_subscription",
        fake_create_subscription,
    )

    response = await billing_routes.subscribe(
        billing_routes.SubscribeRequest(plan="pro"),
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert response.client_secret is None
    assert response.subscription_id is None
    assert response.type == ""


@pytest.mark.asyncio
async def test_subscribe_redacts_credentials_from_billing_errors(monkeypatch) -> None:
    async def fake_create_subscription(self, user_id, plan):
        raise billing_routes.BillingError(
            "Stripe failed https://user:url-secret@example.test/session"
            "?client_secret=query-secret authorization=Bearer bearer-secret "
            "for operator@example.test"
        )

    monkeypatch.setattr(
        billing_routes.BillingService,
        "create_subscription",
        fake_create_subscription,
    )

    with pytest.raises(billing_routes.HTTPException) as exc_info:
        await billing_routes.subscribe(
            billing_routes.SubscribeRequest(plan="pro"),
            current_user=SimpleNamespace(id="user-1"),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == billing_routes.status.HTTP_400_BAD_REQUEST
    assert "url-secret" not in exc_info.value.detail
    assert "query-secret" not in exc_info.value.detail
    assert "bearer-secret" not in exc_info.value.detail
    assert "operator@example.test" not in exc_info.value.detail
    assert "https://***@example.test/session" in exc_info.value.detail
    assert "client_secret=***" in exc_info.value.detail
    assert "authorization=Bearer ***" in exc_info.value.detail
    assert "[redacted-email]" in exc_info.value.detail


@pytest.mark.asyncio
async def test_list_payment_methods_normalizes_malformed_fields(monkeypatch) -> None:
    async def fake_get_payment_methods(self, user_id):
        return [
            {
                "id": ["pm_123"],
                "brand": {"brand": "visa"},
                "last4": 1234,
                "exp_month": " 8 ",
                "exp_year": "2027",
                "is_default": "yes",
            }
        ]

    monkeypatch.setattr(
        billing_routes.BillingService,
        "get_payment_methods",
        fake_get_payment_methods,
    )

    response = await billing_routes.list_payment_methods(
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert len(response) == 1
    assert response[0].id == ""
    assert response[0].brand == "unknown"
    assert response[0].last4 == ""
    assert response[0].exp_month == 8
    assert response[0].exp_year == 2027
    assert response[0].is_default is True


@pytest.mark.asyncio
async def test_list_invoices_normalizes_malformed_fields(monkeypatch) -> None:
    async def fake_get_invoices(self, user_id):
        return [
            {
                "id": ["inv_123"],
                "number": {"number": "001"},
                "status": ["paid"],
                "amount_due": " 1200 ",
                "amount_paid": "1000",
                "currency": {"currency": "usd"},
                "period_start": ["2026-01-01T00:00:00Z"],
                "period_end": {"end": "2026-01-31T00:00:00Z"},
                "hosted_invoice_url": ["https://example.com/invoice"],
                "pdf": {"pdf": "https://example.com/invoice.pdf"},
                "created": " 2026-02-01T00:00:00Z ",
            }
        ]

    monkeypatch.setattr(
        billing_routes.BillingService,
        "get_invoices",
        fake_get_invoices,
    )

    response = await billing_routes.list_invoices(
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert len(response) == 1
    assert response[0].id == ""
    assert response[0].number is None
    assert response[0].status is None
    assert response[0].amount_due == 1200
    assert response[0].amount_paid == 1000
    assert response[0].currency == ""
    assert response[0].period_start == ""
    assert response[0].period_end == ""
    assert response[0].hosted_invoice_url is None
    assert response[0].pdf is None
    assert response[0].created == "2026-02-01T00:00:00Z"


def test_invoice_response_allows_safe_public_urls() -> None:
    response = billing_routes._invoice_to_response(
        {
            "hosted_invoice_url": " https://invoice.stripe.com/i/inv_123 ",
            "pdf": "https://pay.stripe.com/invoice/inv_123/pdf?download=1",
        },
    )

    assert response.hosted_invoice_url == "https://invoice.stripe.com/i/inv_123"
    assert response.pdf == "https://pay.stripe.com/invoice/inv_123/pdf?download=1"


def test_invoice_response_rejects_unsafe_public_urls() -> None:
    response = billing_routes._invoice_to_response(
        {
            "hosted_invoice_url": "https://user:secret@invoice.stripe.com/i/inv_123",
            "pdf": "https://pay.stripe.com/invoice/inv_123/pdf?client_secret=secret",
        },
    )

    assert response.hosted_invoice_url is None
    assert response.pdf is None


@pytest.mark.asyncio
async def test_confirm_subscription_normalizes_malformed_fields(monkeypatch) -> None:
    async def fake_confirm_subscription(self, user_id, subscription_id):
        return {
            "plan": ["pro"],
            "credits": " 100 ",
            "subscription_id": {"id": "sub_123"},
            "status": ["active"],
        }

    monkeypatch.setattr(
        billing_routes.BillingService,
        "confirm_subscription",
        fake_confirm_subscription,
    )

    response = await billing_routes.confirm_subscription(
        billing_routes.ConfirmSubscriptionRequest(subscription_id="sub_123"),
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert response.plan == ""
    assert response.credits == 100
    assert response.subscription_id == ""
    assert response.status == ""


@pytest.mark.asyncio
async def test_change_plan_normalizes_malformed_fields(monkeypatch) -> None:
    async def fake_change_plan(self, user_id, plan):
        return {
            "old_plan": ["starter"],
            "new_plan": {"plan": "pro"},
            "credits": " 250 ",
            "subscription_id": ["sub_123"],
        }

    monkeypatch.setattr(
        billing_routes.BillingService,
        "change_plan",
        fake_change_plan,
    )

    response = await billing_routes.change_plan(
        billing_routes.ChangePlanRequest(plan="pro"),
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert response.old_plan == ""
    assert response.new_plan == ""
    assert response.credits == 250
    assert response.subscription_id is None


@pytest.mark.asyncio
async def test_cancel_subscription_normalizes_malformed_fields(monkeypatch) -> None:
    async def fake_cancel_subscription(self, user_id):
        return {
            "status": ["canceled"],
            "access_until": {"date": "2026-05-01T00:00:00Z"},
            "subscription_id": ["sub_123"],
        }

    monkeypatch.setattr(
        billing_routes.BillingService,
        "cancel_subscription",
        fake_cancel_subscription,
    )

    response = await billing_routes.cancel_subscription(
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert response.status == ""
    assert response.access_until == ""
    assert response.subscription_id is None


@pytest.mark.asyncio
async def test_topup_normalizes_malformed_fields(monkeypatch) -> None:
    async def fake_create_topup_payment(self, user_id, package):
        return {
            "client_secret": ["pi_secret_123"],
        }

    monkeypatch.setattr(
        billing_routes.BillingService,
        "create_topup_payment",
        fake_create_topup_payment,
    )

    response = await billing_routes.topup(
        billing_routes.TopupRequest(package="starter"),
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert response.client_secret == ""


@pytest.mark.asyncio
async def test_add_payment_method_normalizes_malformed_fields(monkeypatch) -> None:
    async def fake_add_payment_method(self, user_id):
        return {
            "client_secret": ["seti_123"],
        }

    monkeypatch.setattr(
        billing_routes.BillingService,
        "add_payment_method",
        fake_add_payment_method,
    )

    response = await billing_routes.add_payment_method(
        current_user=SimpleNamespace(id="user-1"),
        db=SimpleNamespace(),
    )

    assert response.client_secret == ""
