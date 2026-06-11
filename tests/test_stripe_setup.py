from __future__ import annotations

import asyncio
from types import SimpleNamespace

import codey.saas.billing.stripe_setup as stripe_setup


class _FailingMetadata:
    def to_dict_recursive(self):
        raise RuntimeError("metadata unavailable")


def test_fetch_existing_products_ignores_non_string_codey_entity(monkeypatch) -> None:
    products = [
        SimpleNamespace(id="prod_bad", metadata={stripe_setup._METADATA_APP_KEY: ["plan:pro"]}),
        SimpleNamespace(id="prod_good", metadata={stripe_setup._METADATA_APP_KEY: "plan:pro"}),
    ]

    class _ProductList:
        def auto_paging_iter(self):
            return iter(products)

    monkeypatch.setattr(
        stripe_setup.stripe.Product,
        "list",
        lambda limit=100, active=True: _ProductList(),
    )

    result = stripe_setup._fetch_existing_products()

    assert list(result.keys()) == ["plan:pro"]
    assert result["plan:pro"].id == "prod_good"


def test_fetch_existing_products_ignores_unreadable_metadata(monkeypatch) -> None:
    products = [
        SimpleNamespace(id="prod_bad", metadata=_FailingMetadata()),
        SimpleNamespace(id="prod_good", metadata={stripe_setup._METADATA_APP_KEY: "plan:pro"}),
    ]

    class _ProductList:
        def auto_paging_iter(self):
            return iter(products)

    monkeypatch.setattr(
        stripe_setup.stripe.Product,
        "list",
        lambda limit=100, active=True: _ProductList(),
    )

    result = stripe_setup._fetch_existing_products()

    assert list(result.keys()) == ["plan:pro"]
    assert result["plan:pro"].id == "prod_good"


def test_catalog_has_ids_rejects_whitespace_price_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        stripe_setup,
        "PLANS",
        {
            "free": {"price_monthly": 0},
            "pro": {"price_monthly": 2000, "stripe_price_id": "   "},
        },
    )
    monkeypatch.setattr(
        stripe_setup,
        "TOPUP_PACKAGES",
        {
            "small": {"stripe_price_id": "price_topup_small"},
        },
    )

    assert stripe_setup._catalog_has_ids() is False


def test_catalog_has_ids_rejects_control_character_price_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        stripe_setup,
        "PLANS",
        {
            "free": {"price_monthly": 0},
            "pro": {"price_monthly": 2000, "stripe_price_id": "price\nbad"},
        },
    )
    monkeypatch.setattr(
        stripe_setup,
        "TOPUP_PACKAGES",
        {
            "small": {"stripe_price_id": "price_topup_small"},
        },
    )

    assert stripe_setup._catalog_has_ids() is False


def test_catalog_has_ids_rejects_internal_whitespace_price_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        stripe_setup,
        "PLANS",
        {
            "free": {"price_monthly": 0},
            "pro": {"price_monthly": 2000, "stripe_price_id": "price bad"},
        },
    )
    monkeypatch.setattr(
        stripe_setup,
        "TOPUP_PACKAGES",
        {
            "small": {"stripe_price_id": "price_topup_small"},
        },
    )

    assert stripe_setup._catalog_has_ids() is False


def test_setup_stripe_products_falls_back_from_whitespace_env_key(monkeypatch) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "   ")
    monkeypatch.setattr(stripe_setup.settings, "stripe_secret_key", "sk_live_settings")
    monkeypatch.setattr(stripe_setup, "PLANS", {})
    monkeypatch.setattr(stripe_setup, "TOPUP_PACKAGES", {})
    monkeypatch.setattr(stripe_setup, "_require_stripe", lambda: None)
    monkeypatch.setattr(stripe_setup, "_fetch_existing_products", lambda: {})

    asyncio.run(stripe_setup.setup_stripe_products())

    assert stripe_setup.stripe.api_key == "sk_live_settings"


def test_setup_stripe_products_falls_back_from_internal_whitespace_env_key(
    monkeypatch,
) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live bad")
    monkeypatch.setattr(stripe_setup.settings, "stripe_secret_key", "sk_live_settings")
    monkeypatch.setattr(stripe_setup, "PLANS", {})
    monkeypatch.setattr(stripe_setup, "TOPUP_PACKAGES", {})
    monkeypatch.setattr(stripe_setup, "_require_stripe", lambda: None)
    monkeypatch.setattr(stripe_setup, "_fetch_existing_products", lambda: {})

    asyncio.run(stripe_setup.setup_stripe_products())

    assert stripe_setup.stripe.api_key == "sk_live_settings"


def test_setup_stripe_products_falls_back_from_control_character_env_key(
    monkeypatch,
) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_\ninvalid")
    monkeypatch.setattr(stripe_setup.settings, "stripe_secret_key", "sk_live_settings")
    monkeypatch.setattr(stripe_setup, "PLANS", {})
    monkeypatch.setattr(stripe_setup, "TOPUP_PACKAGES", {})
    monkeypatch.setattr(stripe_setup, "_require_stripe", lambda: None)
    monkeypatch.setattr(stripe_setup, "_fetch_existing_products", lambda: {})

    asyncio.run(stripe_setup.setup_stripe_products())

    assert stripe_setup.stripe.api_key == "sk_live_settings"
