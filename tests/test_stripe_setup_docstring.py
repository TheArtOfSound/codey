from __future__ import annotations

from codey.saas.billing import stripe_setup


def test_setup_stripe_products_keeps_public_docstring() -> None:
    assert stripe_setup.setup_stripe_products.__doc__ is not None
    assert "Create Stripe Products and Prices" in stripe_setup.setup_stripe_products.__doc__
