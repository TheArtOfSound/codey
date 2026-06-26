from __future__ import annotations

from codey.saas.billing.plans import PLANS, TOPUP_PACKAGES

__all__ = [
    "BillingError",
    "BillingService",
    "PLANS",
    "TOPUP_PACKAGES",
    "handle_stripe_webhook",
]


def __getattr__(name: str):
    if name in {"BillingError", "BillingService"}:
        from codey.saas.billing.service import BillingError, BillingService

        exports = {
            "BillingError": BillingError,
            "BillingService": BillingService,
        }
        return exports[name]
    if name == "handle_stripe_webhook":
        from codey.saas.billing.webhooks import handle_stripe_webhook

        return handle_stripe_webhook
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
