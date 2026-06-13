from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    import stripe
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-light tests
    if exc.name != "stripe":
        raise
    _STRIPE_IMPORT_ERROR: ModuleNotFoundError | None = exc

    def _raise_missing_stripe(*args, **kwargs):
        raise RuntimeError("stripe is required for Stripe catalog setup") from _STRIPE_IMPORT_ERROR

    class _MissingStripeResource:
        create = staticmethod(_raise_missing_stripe)
        list = staticmethod(_raise_missing_stripe)

    class _MissingStripe:
        api_key = ""
        Product = _MissingStripeResource
        Price = _MissingStripeResource

    stripe: Any = _MissingStripe()
else:  # pragma: no cover - depends on optional runtime dependency
    _STRIPE_IMPORT_ERROR = None

from codey.saas.billing.plans import PLANS, TOPUP_PACKAGES
from codey.saas.config import settings

logger = logging.getLogger(__name__)

_METADATA_APP_KEY = "codey_entity"
_catalog_lock = asyncio.Lock()
_catalog_ready = False


def _require_stripe() -> None:
    if _STRIPE_IMPORT_ERROR is not None:
        raise RuntimeError("stripe is required for Stripe catalog setup") from _STRIPE_IMPORT_ERROR


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_non_empty_stripe_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None
    return normalized or None


def _metadata_lookup(metadata: object, key: str) -> str | None:
    value: object | None
    if metadata is None:
        return None
    if isinstance(metadata, dict):
        value = metadata.get(key)
    elif hasattr(metadata, "to_dict_recursive"):
        try:
            payload = metadata.to_dict_recursive()
        except Exception:
            return None
        value = payload.get(key) if isinstance(payload, dict) else None
    elif hasattr(metadata, "to_dict"):
        try:
            payload = metadata.to_dict()
        except Exception:
            return None
        value = payload.get(key) if isinstance(payload, dict) else None
    else:
        try:
            value = metadata[key]  # type: ignore[index]
        except Exception:
            value = getattr(metadata, key, None)
    return _coerce_non_empty_stripe_text(value)


def _catalog_has_ids() -> bool:
    plans_ready = all(
        plan["price_monthly"] == 0
        or _coerce_non_empty_stripe_text(plan.get("stripe_price_id")) is not None
        for plan in PLANS.values()
    )
    topups_ready = all(
        _coerce_non_empty_stripe_text(pkg.get("stripe_price_id")) is not None
        for pkg in TOPUP_PACKAGES.values()
    )
    return plans_ready and topups_ready


async def ensure_stripe_catalog_loaded() -> bool:
    global _catalog_ready

    if _catalog_ready or _catalog_has_ids():
        _catalog_ready = True
        return True

    async with _catalog_lock:
        if _catalog_ready or _catalog_has_ids():
            _catalog_ready = True
            return True

        await setup_stripe_products()
        _catalog_ready = _catalog_has_ids()
        return _catalog_ready


async def setup_stripe_products() -> None:
    """Create Stripe Products and Prices for all paid plans and top-up packages.

    Safe to call multiple times by checking for existing ``codey_entity``
    metadata tags on Stripe products.
    """
    global _catalog_ready
    # Set API key at call time, not import time — ensures secret file is loaded
    import os
    key = _coerce_non_empty_stripe_text(os.environ.get("STRIPE_SECRET_KEY")) or ""
    if not key or key.startswith("mk_"):
        key = _coerce_non_empty_stripe_text(settings.stripe_secret_key) or ""
    stripe.api_key = key
    if not key or not key.startswith("sk_"):
        logger.warning("Stripe setup skipped: missing valid Stripe secret key")
        _catalog_ready = False
        return
    _require_stripe()
    existing = _fetch_existing_products()

    # ---- subscription plans ------------------------------------------------
    for plan_key, plan in PLANS.items():
        if plan["price_monthly"] == 0:
            continue  # free tier has no Stripe product

        meta_value = f"plan_{plan_key}"

        if meta_value in existing:
            product = existing[meta_value]
            plan["stripe_product_id"] = product.id
            # Find the active recurring price for this product
            price = _find_active_price(product.id, recurring=True)
            if price:
                plan["stripe_price_id"] = price.id
                logger.info(
                    "Plan '%s' already exists (product=%s, price=%s)",
                    plan_key,
                    product.id,
                    price.id,
                )
            else:
                price = stripe.Price.create(
                    product=product.id,
                    unit_amount=plan["price_monthly"],
                    currency="usd",
                    recurring={"interval": "month"},
                    metadata={_METADATA_APP_KEY: meta_value},
                )
                plan["stripe_price_id"] = price.id
                logger.info(
                    "Created price %s for existing product %s (plan '%s')",
                    price.id,
                    product.id,
                    plan_key,
                )
            continue

        product = stripe.Product.create(
            name=f"Codey {plan['name']}",
            description=f"Codey {plan['name']} — {plan['credits']} credits/mo",
            metadata={_METADATA_APP_KEY: meta_value},
        )
        price = stripe.Price.create(
            product=product.id,
            unit_amount=plan["price_monthly"],
            currency="usd",
            recurring={"interval": "month"},
            metadata={_METADATA_APP_KEY: meta_value},
        )
        plan["stripe_product_id"] = product.id
        plan["stripe_price_id"] = price.id
        logger.info(
            "Created product %s + price %s for plan '%s'",
            product.id,
            price.id,
            plan_key,
        )

    # ---- top-up packages ---------------------------------------------------
    for pkg_key, pkg in TOPUP_PACKAGES.items():
        meta_value = f"topup_{pkg_key}"

        if meta_value in existing:
            product = existing[meta_value]
            pkg["stripe_product_id"] = product.id
            price = _find_active_price(product.id, recurring=False)
            if price:
                pkg["stripe_price_id"] = price.id
                logger.info(
                    "Top-up '%s' already exists (product=%s, price=%s)",
                    pkg_key,
                    product.id,
                    price.id,
                )
            else:
                price = stripe.Price.create(
                    product=product.id,
                    unit_amount=pkg["price"],
                    currency="usd",
                    metadata={_METADATA_APP_KEY: meta_value},
                )
                pkg["stripe_price_id"] = price.id
                logger.info(
                    "Created price %s for existing top-up product %s ('%s')",
                    price.id,
                    product.id,
                    pkg_key,
                )
            continue

        product = stripe.Product.create(
            name=f"Codey {pkg['label']}",
            description=f"{pkg['credits']} bonus credits",
            metadata={_METADATA_APP_KEY: meta_value},
        )
        price = stripe.Price.create(
            product=product.id,
            unit_amount=pkg["price"],
            currency="usd",
            metadata={_METADATA_APP_KEY: meta_value},
        )
        pkg["stripe_product_id"] = product.id
        pkg["stripe_price_id"] = price.id
        logger.info(
            "Created product %s + price %s for top-up '%s'",
            product.id,
            price.id,
            pkg_key,
        )

    _catalog_ready = _catalog_has_ids()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_existing_products() -> dict[str, stripe.Product]:
    """Return a dict mapping ``codey_entity`` metadata value -> Product."""
    _require_stripe()
    result: dict[str, stripe.Product] = {}
    products = stripe.Product.list(limit=100, active=True)
    for product in products.auto_paging_iter():
        entity = _metadata_lookup(getattr(product, "metadata", None), _METADATA_APP_KEY)
        if entity:
            result[entity] = product
    return result


def _find_active_price(
    product_id: str, *, recurring: bool
) -> stripe.Price | None:
    """Find the first active price for a product, filtered by type."""
    _require_stripe()
    prices = stripe.Price.list(product=product_id, active=True, limit=10)
    for price in prices.data:
        if recurring and price.recurring is not None:
            return price
        if not recurring and price.recurring is None:
            return price
    return None
