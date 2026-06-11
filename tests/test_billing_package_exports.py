from __future__ import annotations

import codey.saas.billing as billing
from codey.saas.billing.service import BillingError, BillingService
from codey.saas.billing.webhooks import handle_stripe_webhook


def test_billing_package_exports_are_lazy_and_stable() -> None:
    assert billing.PLANS["free"]["credits"] == 10
    assert billing.TOPUP_PACKAGES["starter_pack"]["credits"] == 50
    assert billing.BillingError is BillingError
    assert billing.BillingService is BillingService
    assert billing.handle_stripe_webhook is handle_stripe_webhook
