from __future__ import annotations

import logging
import math
import re
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
_URL_CREDENTIAL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&#](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|token|secret|password)=)[^&#\s]+"
)
_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|token|secret|password|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)

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


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_non_empty_webhook_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None
    return normalized or None


def _redact_webhook_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _NAMED_SECRET_RE.sub(r"\1***", text)
    return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)


stripe.api_key = _coerce_non_empty_webhook_text(settings.stripe_secret_key) or ""


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
        webhook_secret = _coerce_non_empty_webhook_text(settings.stripe_webhook_secret) or ""
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
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


def _coerce_stripe_metadata(value: object) -> dict[str, str]:
    if isinstance(value, dict):
        payload = value
    elif hasattr(value, "to_dict_recursive"):
        try:
            payload = value.to_dict_recursive()
        except Exception:
            return {}
    elif hasattr(value, "to_dict"):
        try:
            payload = value.to_dict()
        except Exception:
            return {}
    else:
        return {}

    if not isinstance(payload, dict):
        return {}
    return {
        str(key): normalized
        for key, item in payload.items()
        for normalized in [_coerce_non_empty_webhook_text(item)]
        if isinstance(key, str) and normalized is not None
    }


def _coerce_stripe_timestamp(value: object) -> datetime | None:
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

    metadata = _coerce_stripe_metadata(subscription.get("metadata"))
    plan = metadata.get("codey_plan")
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

    period_end = _coerce_stripe_timestamp(subscription.get("current_period_end"))
    if period_end is not None:
        user.subscription_period_end = period_end

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
    metadata = _coerce_stripe_metadata(subscription.get("metadata"))
    new_plan = metadata.get("codey_plan")

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
    period_end = _coerce_stripe_timestamp(subscription.get("current_period_end"))
    if period_end is not None:
        user.subscription_period_end = period_end

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
            period_end = _coerce_stripe_timestamp(sub.current_period_end)
            if period_end is not None:
                user.subscription_period_end = period_end
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

    email = _coerce_non_empty_webhook_text(getattr(user, "email", None))
    if email:
        try:
            from codey.saas.emails.service import EmailService

            await EmailService().send_payment_failed(email)
        except Exception as exc:
            logger.warning(
                "invoice.payment_failed: payment failed email skipped for user=%s: %s",
                user.id,
                _redact_webhook_error(exc),
            )

    logger.warning(
        "invoice.payment_failed: user=%s marked past_due (invoice=%s)",
        user.id,
        invoice["id"],
    )
    return {"status": "ok", "event": "invoice.payment_failed"}


async def _handle_payment_intent_succeeded(
    payment_intent: dict, db: AsyncSession
) -> dict:
    """payment_intent.succeeded — check if this is a top-up purchase and add credits."""
    metadata = _coerce_stripe_metadata(payment_intent.get("metadata"))

    # Only process codey top-up PaymentIntents
    if metadata.get("type") != "codey_topup":
        return {
            "status": "ok",
            "event": "payment_intent.succeeded",
            "action": "not_a_topup",
        }

    payment_intent_id = _coerce_non_empty_webhook_text(payment_intent.get("id"))
    if payment_intent_id is None:
        logger.error("payment_intent.succeeded: missing payment intent id")
        return {"status": "error", "reason": "missing_payment_intent_id"}

    user_id_str = metadata.get("user_id")
    package_key = metadata.get("package")
    credits_str = metadata.get("credits")

    if not all([user_id_str, package_key, credits_str]):
        logger.error(
            "payment_intent.succeeded: incomplete topup metadata on %s: %s",
            payment_intent_id,
            metadata,
        )
        return {"status": "error", "reason": "incomplete_metadata"}

    try:
        user_id = UUID(user_id_str)
        credits_amount = int(credits_str)
    except (ValueError, TypeError) as exc:
        logger.error(
            "payment_intent.succeeded: bad metadata values on %s: %s",
            payment_intent_id,
            exc,
        )
        return {"status": "error", "reason": "bad_metadata_values"}

    # Validate the credits match the package definition (tamper check)
    pkg = TOPUP_PACKAGES.get(package_key)
    if not pkg:
        logger.error(
            "payment_intent.succeeded: invalid package metadata on %s: %s",
            payment_intent_id,
            package_key,
        )
        return {"status": "error", "reason": "invalid_package_metadata"}
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
        stripe_payment_intent_id=payment_intent_id,
    )

    await db.flush()

    logger.info(
        "payment_intent.succeeded: user=%s topup=%s credits=%d pi=%s",
        user_id,
        package_key,
        credits_amount,
        payment_intent_id,
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
    customer_id = _coerce_non_empty_webhook_text(customer_id)
    if customer_id is None:
        return None
    stmt = select(User).where(User.stripe_customer_id == customer_id)
    if lock:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _map_subscription_status(stripe_status: object) -> str:
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
    if not isinstance(stripe_status, str):
        return "incomplete"
    normalized = stripe_status.strip()
    if not normalized:
        return "incomplete"
    return mapping.get(normalized, normalized)
