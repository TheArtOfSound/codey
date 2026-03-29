from __future__ import annotations

import logging

import stripe

from codey.saas.billing.plans import PLANS, TOPUP_PACKAGES
from codey.saas.config import settings

logger = logging.getLogger(__name__)

_METADATA_APP_KEY = "codey_entity"


async def setup_stripe_products() -> None:
    # Set API key at call time, not import time — ensures secret file is loaded
    import os
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY") or settings.stripe_secret_key
    if not stripe.api_key or stripe.api_key.startswith("mk_"):
        logger.warning("Stripe setup skipped: no valid API key")
        return
    """Create Stripe Products and Prices for all paid plans and top-up packages.

    Safe to call multiple times — skips anything that already exists by checking
    for a ``codey_entity`` metadata tag on existing products.
    """
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_existing_products() -> dict[str, stripe.Product]:
    """Return a dict mapping ``codey_entity`` metadata value -> Product."""
    result: dict[str, stripe.Product] = {}
    products = stripe.Product.list(limit=100, active=True)
    for product in products.auto_paging_iter():
        entity = product.metadata.get(_METADATA_APP_KEY)
        if entity:
            result[entity] = product
    return result


def _find_active_price(
    product_id: str, *, recurring: bool
) -> stripe.Price | None:
    """Find the first active price for a product, filtered by type."""
    prices = stripe.Price.list(product=product_id, active=True, limit=10)
    for price in prices.data:
        if recurring and price.recurring is not None:
            return price
        if not recurring and price.recurring is None:
            return price
    return None
