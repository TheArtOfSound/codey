from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.billing.plans import PLANS, TOPUP_PACKAGES
from codey.saas.config import settings
from codey.saas.credits.service import CreditService
from codey.saas.models import User

logger = logging.getLogger(__name__)

stripe.api_key = settings.stripe_secret_key

# Events we care about — everything else is acknowledged and ignored.
_HANDLED_EVENTS = frozenset(
    {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.payment_succeeded",
        "invoice.payment_failed",
        "payment_intent.succeeded",
    }
)


async def handle_stripe_webhook(
    payload: bytes,
    sig_header: str,
    db: AsyncSession,
) -> dict:
    """Verify and dispatch a Stripe webhook event.

    Returns a dict with ``{"status": "ok", ...}`` on success or raises on
    signature failure.  Unknown event types are acknowledged silently so Stripe
    stops retrying them.
    """
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook signature verification failed")
        raise
    except ValueError:
        logger.warning("Stripe webhook payload could not be parsed")
        raise

    event_type: str = event["type"]
    data_object = event["data"]["object"]

    if event_type not in _HANDLED_EVENTS:
        logger.debug("Ignoring unhandled Stripe event: %s", event_type)
        return {"status": "ignored", "event": event_type}

    logger.info("Handling Stripe event: %s (id=%s)", event_type, event["id"])

    handler = _EVENT_HANDLERS.get(event_type)
    if handler:
        return await handler(data_object, db)

    return {"status": "ok", "event": event_type}


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


async def _handle_subscription_created(
    subscription: dict, db: AsyncSession
) -> dict:
    """customer.subscription.created — set the user's plan and initial credits."""
    customer_id = subscription["customer"]
    user = await _get_user_by_customer(customer_id, db)
    if user is None:
        logger.error(
            "subscription.created: no user for customer %s", customer_id
        )
        return {"status": "error", "reason": "user_not_found"}

    plan = subscription.get("metadata", {}).get("codey_plan")
    if not plan or plan not in PLANS:
        logger.error(
            "subscription.created: missing or invalid codey_plan metadata "
            "on subscription %s",
            subscription["id"],
        )
        return {"status": "error", "reason": "invalid_plan_metadata"}

    user.plan = plan
    user.plan_status = _map_subscription_status(subscription["status"])
    user.subscription_id = subscription["id"]
    user.credits_remaining = PLANS[plan]["credits"]
    user.credits_used_this_month = 0

    period_end = subscription.get("current_period_end")
    if period_end:
        user.subscription_period_end = datetime.fromtimestamp(
            period_end, tz=timezone.utc
        )

    await db.flush()

    logger.info(
        "subscription.created: user=%s plan=%s sub=%s",
        user.id,
        plan,
        subscription["id"],
    )
    return {"status": "ok", "event": "customer.subscription.created"}


async def _handle_subscription_updated(
    subscription: dict, db: AsyncSession
) -> dict:
    """customer.subscription.updated — handle plan changes, cancellation, reactivation."""
    customer_id = subscription["customer"]
    user = await _get_user_by_customer(customer_id, db, lock=True)
    if user is None:
        logger.error(
            "subscription.updated: no user for customer %s", customer_id
        )
        return {"status": "error", "reason": "user_not_found"}

    new_status = _map_subscription_status(subscription["status"])
    new_plan = subscription.get("metadata", {}).get("codey_plan")

    # Detect plan change (upgrade / downgrade)
    if new_plan and new_plan in PLANS and new_plan != user.plan:
        old_plan = user.plan
        user.plan = new_plan
        logger.info(
            "subscription.updated: user=%s plan change %s -> %s",
            user.id,
            old_plan,
            new_plan,
        )

    # Handle cancel_at_period_end
    if subscription.get("cancel_at_period_end"):
        user.plan_status = "cancelling"
    else:
        user.plan_status = new_status

    # Update period end
    period_end = subscription.get("current_period_end")
    if period_end:
        user.subscription_period_end = datetime.fromtimestamp(
            period_end, tz=timezone.utc
        )

    await db.flush()

    logger.info(
        "subscription.updated: user=%s status=%s plan=%s",
        user.id,
        user.plan_status,
        user.plan,
    )
    return {"status": "ok", "event": "customer.subscription.updated"}


async def _handle_subscription_deleted(
    subscription: dict, db: AsyncSession
) -> dict:
    """customer.subscription.deleted — subscription fully cancelled or expired."""
    customer_id = subscription["customer"]
    user = await _get_user_by_customer(customer_id, db, lock=True)
    if user is None:
        logger.error(
            "subscription.deleted: no user for customer %s", customer_id
        )
        return {"status": "error", "reason": "user_not_found"}

    logger.info(
        "subscription.deleted: user=%s was on plan=%s sub=%s",
        user.id,
        user.plan,
        subscription["id"],
    )

    user.plan = "free"
    user.plan_status = "cancelled"
    user.subscription_id = None
    user.subscription_period_end = None
    # Reset to free-tier credits; keep any purchased topup credits
    user.credits_remaining = PLANS["free"]["credits"]
    user.credits_used_this_month = 0

    await db.flush()
    return {"status": "ok", "event": "customer.subscription.deleted"}


async def _handle_invoice_payment_succeeded(
    invoice: dict, db: AsyncSession
) -> dict:
    """invoice.payment_succeeded — add monthly credits on renewal invoices.

    First invoices are handled by subscription.created, so we skip them here
    to avoid double-crediting.
    """
    # billing_reason: "subscription_cycle" = renewal, "subscription_create" = first
    billing_reason = invoice.get("billing_reason")
    if billing_reason != "subscription_cycle":
        logger.debug(
            "invoice.payment_succeeded: skipping billing_reason=%s",
            billing_reason,
        )
        return {
            "status": "ok",
            "event": "invoice.payment_succeeded",
            "action": "skipped_non_renewal",
        }

    customer_id = invoice["customer"]
    user = await _get_user_by_customer(customer_id, db, lock=True)
    if user is None:
        logger.error(
            "invoice.payment_succeeded: no user for customer %s", customer_id
        )
        return {"status": "error", "reason": "user_not_found"}

    # Reset to active if they were past_due
    if user.plan_status == "past_due":
        user.plan_status = "active"

    credit_service = CreditService(db)
    await credit_service.add_monthly_credits(user.id)

    # Update period end from the subscription
    sub_id = invoice.get("subscription")
    if sub_id:
        try:
            sub = stripe.Subscription.retrieve(sub_id)
            user.subscription_period_end = datetime.fromtimestamp(
                sub.current_period_end, tz=timezone.utc
            )
        except stripe.error.StripeError:
            logger.warning(
                "Could not retrieve subscription %s for period end update",
                sub_id,
            )

    await db.flush()

    logger.info(
        "invoice.payment_succeeded: user=%s renewal credits added for plan=%s",
        user.id,
        user.plan,
    )
    return {"status": "ok", "event": "invoice.payment_succeeded", "action": "credits_added"}


async def _handle_invoice_payment_failed(
    invoice: dict, db: AsyncSession
) -> dict:
    """invoice.payment_failed — mark subscription as past_due."""
    customer_id = invoice["customer"]
    user = await _get_user_by_customer(customer_id, db, lock=True)
    if user is None:
        logger.error(
            "invoice.payment_failed: no user for customer %s", customer_id
        )
        return {"status": "error", "reason": "user_not_found"}

    user.plan_status = "past_due"
    await db.flush()

    logger.warning(
        "invoice.payment_failed: user=%s marked past_due (invoice=%s)",
        user.id,
        invoice["id"],
    )
    # Email trigger is handled separately by the notification system
    return {"status": "ok", "event": "invoice.payment_failed"}


async def _handle_payment_intent_succeeded(
    payment_intent: dict, db: AsyncSession
) -> dict:
    """payment_intent.succeeded — check if this is a top-up purchase and add credits."""
    metadata = payment_intent.get("metadata", {})

    # Only process codey top-up PaymentIntents
    if metadata.get("type") != "codey_topup":
        return {
            "status": "ok",
            "event": "payment_intent.succeeded",
            "action": "not_a_topup",
        }

    user_id_str = metadata.get("user_id")
    package_key = metadata.get("package")
    credits_str = metadata.get("credits")

    if not all([user_id_str, package_key, credits_str]):
        logger.error(
            "payment_intent.succeeded: incomplete topup metadata on %s: %s",
            payment_intent["id"],
            metadata,
        )
        return {"status": "error", "reason": "incomplete_metadata"}

    try:
        user_id = UUID(user_id_str)
        credits_amount = int(credits_str)
    except (ValueError, TypeError) as exc:
        logger.error(
            "payment_intent.succeeded: bad metadata values on %s: %s",
            payment_intent["id"],
            exc,
        )
        return {"status": "error", "reason": "bad_metadata_values"}

    # Validate the credits match the package definition (tamper check)
    pkg = TOPUP_PACKAGES.get(package_key)
    if pkg and pkg["credits"] != credits_amount:
        logger.error(
            "payment_intent.succeeded: credits mismatch for %s — "
            "metadata says %d, package says %d",
            package_key,
            credits_amount,
            pkg["credits"],
        )
        # Use the package definition as the source of truth
        credits_amount = pkg["credits"]

    credit_service = CreditService(db)
    await credit_service.add_topup_credits(
        user_id=user_id,
        amount=credits_amount,
        stripe_payment_intent_id=payment_intent["id"],
    )

    await db.flush()

    logger.info(
        "payment_intent.succeeded: user=%s topup=%s credits=%d pi=%s",
        user_id,
        package_key,
        credits_amount,
        payment_intent["id"],
    )
    return {
        "status": "ok",
        "event": "payment_intent.succeeded",
        "action": "topup_credits_added",
        "credits": credits_amount,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVENT_HANDLERS = {
    "customer.subscription.created": _handle_subscription_created,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "invoice.payment_succeeded": _handle_invoice_payment_succeeded,
    "invoice.payment_failed": _handle_invoice_payment_failed,
    "payment_intent.succeeded": _handle_payment_intent_succeeded,
}


async def _get_user_by_customer(
    customer_id: str,
    db: AsyncSession,
    *,
    lock: bool = False,
) -> User | None:
    """Look up a user by their stripe_customer_id."""
    stmt = select(User).where(User.stripe_customer_id == customer_id)
    if lock:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _map_subscription_status(stripe_status: str) -> str:
    """Map Stripe subscription status to our plan_status values."""
    mapping = {
        "active": "active",
        "trialing": "active",
        "past_due": "past_due",
        "canceled": "cancelled",
        "unpaid": "past_due",
        "incomplete": "incomplete",
        "incomplete_expired": "cancelled",
        "paused": "paused",
    }
    return mapping.get(stripe_status, stripe_status)
