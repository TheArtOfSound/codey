from __future__ import annotations

# ---------------------------------------------------------------------------
# Subscription plans
# ---------------------------------------------------------------------------
# Prices are in cents (USD).  stripe_price_id is populated at runtime by
# stripe_setup.setup_stripe_products() so that we never hard-code Stripe IDs.
# ---------------------------------------------------------------------------

PLANS: dict[str, dict] = {
    "free": {
        "name": "Free",
        "price_monthly": 0,
        "credits": 10,
        "rollover": 0,
        "features": {
            "github_repos": 0,
            "autonomous_mode": False,
            "priority": False,
            "max_upload_mb": 5,
        },
    },
    "starter": {
        "name": "Starter",
        "price_monthly": 1900,
        "credits": 100,
        "rollover": 50,
        "features": {
            "github_repos": 1,
            "autonomous_mode": False,
            "priority": False,
            "max_upload_mb": 50,
        },
        "stripe_price_id": None,
        "stripe_product_id": None,
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 4900,
        "credits": 400,
        "rollover": 200,
        "features": {
            "github_repos": 5,
            "autonomous_mode": True,
            "priority": True,
            "max_upload_mb": 500,
        },
        "stripe_price_id": None,
        "stripe_product_id": None,
    },
    "team": {
        "name": "Team",
        "price_monthly": 14900,
        "credits": 1500,
        "rollover": 750,
        "features": {
            "github_repos": -1,  # unlimited
            "autonomous_mode": True,
            "priority": True,
            "max_upload_mb": 2048,
            "seats": 10,
        },
        "stripe_price_id": None,
        "stripe_product_id": None,
    },
}

# ---------------------------------------------------------------------------
# One-time top-up packages
# ---------------------------------------------------------------------------

TOPUP_PACKAGES: dict[str, dict] = {
    "starter_pack": {
        "credits": 50,
        "price": 900,
        "label": "Starter Pack",
        "stripe_price_id": None,
        "stripe_product_id": None,
    },
    "power_pack": {
        "credits": 200,
        "price": 2900,
        "label": "Power Pack",
        "stripe_price_id": None,
        "stripe_product_id": None,
    },
    "pro_pack": {
        "credits": 600,
        "price": 6900,
        "label": "Pro Pack",
        "stripe_price_id": None,
        "stripe_product_id": None,
    },
    "team_pack": {
        "credits": 2000,
        "price": 19900,
        "label": "Team Pack",
        "stripe_price_id": None,
        "stripe_product_id": None,
    },
}
