from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.billing.plans import PLANS, TOPUP_PACKAGES
from codey.saas.billing.stripe_setup import ensure_stripe_catalog_loaded
from codey.saas.config import settings
from codey.saas.models import User
from codey.saas.referrals import convert_pending_referral

logger = logging.getLogger(__name__)


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_non_empty_billing_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None
    return normalized or None


stripe.api_key = _coerce_non_empty_billing_text(settings.stripe_secret_key) or ""


def _stripe_metadata_lookup(metadata: object, key: str) -> str | None:
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
    return _coerce_non_empty_billing_text(value)


def _stripe_timestamp_to_datetime(value: object) -> datetime | None:
    if isinstance(value, bool):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(timestamp):
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _serialize_stripe_timestamp(value: object) -> str:
    parsed = _stripe_timestamp_to_datetime(value)
    return parsed.isoformat() if parsed is not None else ""


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
        customer_id = _coerce_non_empty_billing_text(getattr(user, "stripe_customer_id", None))
        if not customer_id:
            raise BillingError(
                f"User {user.id} has no Stripe customer — create one first"
            )
        return customer_id

    async def _ensure_customer(self, user: User) -> str:
        """Return the user's Stripe customer id, creating one if absent."""
        existing = _coerce_non_empty_billing_text(getattr(user, "stripe_customer_id", None))
        if existing:
            return existing
        customer = stripe.Customer.create(
            email=_coerce_non_empty_billing_text(getattr(user, "email", None)),
            metadata={"user_id": str(getattr(user, "id", ""))},
        )
        user.stripe_customer_id = customer.id
        await self.db.flush()
        return customer.id

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

        await ensure_stripe_catalog_loaded()
        price_id = _coerce_non_empty_billing_text(PLANS[plan].get("stripe_price_id"))
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

        plan = _stripe_metadata_lookup(getattr(sub, "metadata", None), "codey_plan")
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
        user.subscription_period_end = None
        user.credits_remaining = PLANS[plan]["credits"]
        user.credits_used_this_month = 0
        await convert_pending_referral(self.db, referred_id=user.id)

    async def change_plan(self, user_id: UUID, new_plan: str) -> dict:
        """Upgrade or downgrade an existing subscription with proration."""
        if new_plan not in PLANS or new_plan == "free":
            raise BillingError(f"Invalid target plan: {new_plan}")

        await ensure_stripe_catalog_loaded()
        price_id = _coerce_non_empty_billing_text(PLANS[new_plan].get("stripe_price_id"))
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
            cancel_at_period_end=False,
            proration_behavior="create_prorations",
            metadata={"codey_plan": new_plan},
        )

        old_plan = user.plan if isinstance(user.plan, str) else None
        if old_plan is not None:
            old_plan = old_plan.strip().lower() or None
        current_credits = User._coerce_credit_value(user.credits_remaining)
        user.plan = new_plan
        user.plan_status = "active"
        user.subscription_period_end = None

        # Credit adjustment on upgrade: give the difference immediately
        new_credits = PLANS[new_plan]["credits"]
        bonus = 0
        if old_plan in PLANS:
            old_credits = PLANS[old_plan]["credits"]
            if new_credits > old_credits:
                bonus = new_credits - old_credits
        user.credits_remaining = current_credits + bonus

        await self.db.flush()

        return {
            "old_plan": old_plan or "",
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

        period_end = _stripe_timestamp_to_datetime(sub.current_period_end)
        user.subscription_period_end = period_end
        await self.db.flush()

        return {
            "status": "cancelling",
            "access_until": period_end.isoformat() if period_end is not None else "",
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

        await ensure_stripe_catalog_loaded()
        pkg = TOPUP_PACKAGES[package_key]
        user = await self._get_user(user_id)
        customer_id = await self._ensure_customer(user)

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
        customer = stripe.Customer.retrieve(customer_id)
        invoice_settings = getattr(customer, "invoice_settings", None)
        default_payment_method = (
            getattr(invoice_settings, "default_payment_method", None)
            if invoice_settings is not None
            else None
        )
        if isinstance(default_payment_method, dict):
            default_payment_method = default_payment_method.get("id")

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
                "is_default": pm.id == default_payment_method,
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
                "period_start": _serialize_stripe_timestamp(inv.period_start),
                "period_end": _serialize_stripe_timestamp(inv.period_end),
                "hosted_invoice_url": inv.hosted_invoice_url,
                "pdf": inv.invoice_pdf,
                "created": _serialize_stripe_timestamp(inv.created),
            }
            for inv in invoices.data
        ]
