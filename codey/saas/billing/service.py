from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.billing.plans import PLANS, TOPUP_PACKAGES
from codey.saas.config import settings
from codey.saas.models import User

logger = logging.getLogger(__name__)

stripe.api_key = settings.stripe_secret_key


class BillingError(Exception):
    """Raised for billing-related failures."""


class BillingService:
    """Handles all Stripe payment flows using PaymentIntents, SetupIntents, and
    Subscriptions.  No Checkout Sessions — the frontend renders Codey's own
    payment UI via Stripe Elements (PaymentElement).
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_user(self, user_id: UUID, *, lock: bool = False) -> User:
        stmt = select(User).where(User.id == user_id)
        if lock:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        user = result.scalar_one_or_none()
        if user is None:
            raise BillingError(f"User {user_id} not found")
        return user

    def _require_customer(self, user: User) -> str:
        if not user.stripe_customer_id:
            raise BillingError(
                f"User {user.id} has no Stripe customer — create one first"
            )
        return user.stripe_customer_id

    @staticmethod
    def _has_payment_method(customer_id: str) -> bool:
        methods = stripe.PaymentMethod.list(
            customer=customer_id, type="card", limit=1
        )
        return len(methods.data) > 0

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def create_subscription(self, user_id: UUID, plan: str) -> dict:
        """Start a new subscription using Codey's own payment UI.

        Returns either:
          - {"type": "setup_required", "client_secret": ...}
            → frontend must collect card via SetupIntent + PaymentElement
          - {"type": "payment_required", "client_secret": ..., "subscription_id": ...}
            → frontend must confirm payment via PaymentIntent + PaymentElement
        """
        if plan not in PLANS or plan == "free":
            raise BillingError(f"Invalid paid plan: {plan}")

        price_id = PLANS[plan].get("stripe_price_id")
        if not price_id:
            raise BillingError(
                f"Stripe price not configured for plan '{plan}' — "
                "run setup_stripe_products() first"
            )

        user = await self._get_user(user_id)
        customer_id = self._require_customer(user)

        # If there's already an active subscription, reject
        if user.subscription_id and user.plan_status == "active":
            raise BillingError(
                "User already has an active subscription. "
                "Use change_plan() to switch plans."
            )

        # Step 1: check if customer has a payment method on file
        if not self._has_payment_method(customer_id):
            setup_intent = stripe.SetupIntent.create(
                customer=customer_id,
                payment_method_types=["card"],
                metadata={"user_id": str(user_id), "intended_plan": plan},
            )
            return {
                "type": "setup_required",
                "client_secret": setup_intent.client_secret,
            }

        # Step 2: create subscription — payment_behavior="default_incomplete"
        # means Stripe creates the invoice + PaymentIntent but doesn't auto-charge
        # until the frontend confirms via Elements.
        subscription = stripe.Subscription.create(
            customer=customer_id,
            items=[{"price": price_id}],
            payment_behavior="default_incomplete",
            payment_settings={
                "save_default_payment_method": "on_subscription",
            },
            metadata={"user_id": str(user_id), "codey_plan": plan},
            expand=["latest_invoice.payment_intent"],
        )

        pi = subscription.latest_invoice.payment_intent
        if pi is None:
            # $0 invoice (unlikely for paid plans, but handle gracefully)
            await self._activate_subscription(user, plan, subscription.id)
            return {
                "type": "active",
                "subscription_id": subscription.id,
            }

        return {
            "type": "payment_required",
            "client_secret": pi.client_secret,
            "subscription_id": subscription.id,
        }

    async def confirm_subscription(
        self, user_id: UUID, subscription_id: str
    ) -> dict:
        """Called after the frontend confirms payment.  Activates the plan."""
        user = await self._get_user(user_id, lock=True)

        # Verify the subscription is indeed active/trialing on Stripe's side
        sub = stripe.Subscription.retrieve(subscription_id)
        if sub.status not in ("active", "trialing"):
            raise BillingError(
                f"Subscription {subscription_id} is not active "
                f"(status={sub.status})"
            )

        plan = sub.metadata.get("codey_plan")
        if not plan or plan not in PLANS:
            raise BillingError(
                f"Subscription {subscription_id} missing codey_plan metadata"
            )

        await self._activate_subscription(user, plan, subscription_id)
        await self.db.flush()

        return {
            "plan": plan,
            "credits": user.credits_remaining,
            "subscription_id": subscription_id,
            "status": "active",
        }

    async def _activate_subscription(
        self, user: User, plan: str, subscription_id: str
    ) -> None:
        """Write subscription state to the user row."""
        user.plan = plan
        user.plan_status = "active"
        user.subscription_id = subscription_id
        user.credits_remaining = PLANS[plan]["credits"]
        user.credits_used_this_month = 0

    async def change_plan(self, user_id: UUID, new_plan: str) -> dict:
        """Upgrade or downgrade an existing subscription with proration."""
        if new_plan not in PLANS or new_plan == "free":
            raise BillingError(f"Invalid target plan: {new_plan}")

        price_id = PLANS[new_plan].get("stripe_price_id")
        if not price_id:
            raise BillingError(f"Stripe price not configured for '{new_plan}'")

        user = await self._get_user(user_id, lock=True)
        if not user.subscription_id:
            raise BillingError("No active subscription to modify")

        sub = stripe.Subscription.retrieve(user.subscription_id)
        if sub.status not in ("active", "trialing"):
            raise BillingError(
                f"Subscription is {sub.status} — cannot modify"
            )

        # Swap the single subscription item to the new price
        stripe.Subscription.modify(
            user.subscription_id,
            items=[
                {
                    "id": sub["items"]["data"][0].id,
                    "price": price_id,
                }
            ],
            proration_behavior="create_prorations",
            metadata={"codey_plan": new_plan},
        )

        old_plan = user.plan
        user.plan = new_plan
        user.plan_status = "active"

        # Credit adjustment on upgrade: give the difference immediately
        old_credits = PLANS.get(old_plan, {}).get("credits", 0)
        new_credits = PLANS[new_plan]["credits"]
        if new_credits > old_credits:
            bonus = new_credits - old_credits
            user.credits_remaining += bonus

        await self.db.flush()

        return {
            "old_plan": old_plan,
            "new_plan": new_plan,
            "credits": user.credits_remaining,
            "subscription_id": user.subscription_id,
        }

    async def cancel_subscription(self, user_id: UUID) -> dict:
        """Cancel at period end — user keeps access until the billing period expires."""
        user = await self._get_user(user_id, lock=True)
        if not user.subscription_id:
            raise BillingError("No active subscription to cancel")

        sub = stripe.Subscription.modify(
            user.subscription_id, cancel_at_period_end=True
        )

        user.plan_status = "cancelling"

        period_end = datetime.fromtimestamp(
            sub.current_period_end, tz=timezone.utc
        )
        user.subscription_period_end = period_end
        await self.db.flush()

        return {
            "status": "cancelling",
            "access_until": period_end.isoformat(),
            "subscription_id": user.subscription_id,
        }

    # ------------------------------------------------------------------
    # Top-up purchases
    # ------------------------------------------------------------------

    async def create_topup_payment(
        self, user_id: UUID, package_key: str
    ) -> dict:
        """Create a PaymentIntent for a one-time credit top-up.

        Returns {"client_secret": ...} for the frontend's PaymentElement.
        """
        if package_key not in TOPUP_PACKAGES:
            raise BillingError(f"Unknown top-up package: {package_key}")

        pkg = TOPUP_PACKAGES[package_key]
        user = await self._get_user(user_id)
        customer_id = self._require_customer(user)

        payment_intent = stripe.PaymentIntent.create(
            amount=pkg["price"],
            currency="usd",
            customer=customer_id,
            metadata={
                "user_id": str(user_id),
                "package": package_key,
                "credits": str(pkg["credits"]),
                "type": "codey_topup",
            },
            automatic_payment_methods={"enabled": True},
        )

        return {"client_secret": payment_intent.client_secret}

    # ------------------------------------------------------------------
    # Payment methods
    # ------------------------------------------------------------------

    async def get_payment_methods(self, user_id: UUID) -> list[dict]:
        """List saved cards for the customer."""
        user = await self._get_user(user_id)
        customer_id = self._require_customer(user)

        methods = stripe.PaymentMethod.list(
            customer=customer_id, type="card", limit=20
        )
        return [
            {
                "id": pm.id,
                "brand": pm.card.brand,
                "last4": pm.card.last4,
                "exp_month": pm.card.exp_month,
                "exp_year": pm.card.exp_year,
            }
            for pm in methods.data
        ]

    async def add_payment_method(self, user_id: UUID) -> dict:
        """Create a SetupIntent so the frontend can collect a new card."""
        user = await self._get_user(user_id)
        customer_id = self._require_customer(user)

        setup_intent = stripe.SetupIntent.create(
            customer=customer_id,
            payment_method_types=["card"],
            metadata={"user_id": str(user_id)},
        )
        return {"client_secret": setup_intent.client_secret}

    async def remove_payment_method(
        self, user_id: UUID, payment_method_id: str
    ) -> bool:
        """Detach a payment method from the customer."""
        user = await self._get_user(user_id)
        customer_id = self._require_customer(user)

        # Verify the PM actually belongs to this customer
        pm = stripe.PaymentMethod.retrieve(payment_method_id)
        if pm.customer != customer_id:
            raise BillingError("Payment method does not belong to this user")

        stripe.PaymentMethod.detach(payment_method_id)
        return True

    # ------------------------------------------------------------------
    # Invoices
    # ------------------------------------------------------------------

    async def get_invoices(self, user_id: UUID, limit: int = 10) -> list[dict]:
        """Return recent invoices from Stripe."""
        user = await self._get_user(user_id)
        customer_id = self._require_customer(user)

        invoices = stripe.Invoice.list(
            customer=customer_id, limit=limit, expand=["data.charge"]
        )
        return [
            {
                "id": inv.id,
                "number": inv.number,
                "status": inv.status,
                "amount_due": inv.amount_due,
                "amount_paid": inv.amount_paid,
                "currency": inv.currency,
                "period_start": datetime.fromtimestamp(
                    inv.period_start, tz=timezone.utc
                ).isoformat(),
                "period_end": datetime.fromtimestamp(
                    inv.period_end, tz=timezone.utc
                ).isoformat(),
                "hosted_invoice_url": inv.hosted_invoice_url,
                "pdf": inv.invoice_pdf,
                "created": datetime.fromtimestamp(
                    inv.created, tz=timezone.utc
                ).isoformat(),
            }
            for inv in invoices.data
        ]
