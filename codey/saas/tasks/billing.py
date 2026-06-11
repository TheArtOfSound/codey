from __future__ import annotations

import logging
from datetime import datetime, timedelta

from codey.saas.billing.plans import PLANS
from codey.saas.tasks.asyncio_utils import run_sync_task
from codey.saas.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plan credit limits (mirrored from billing config)
# ---------------------------------------------------------------------------
def _coerce_plan_credit_limit(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        credits = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return credits if credits > 0 else 0


PLAN_MONTHLY_CREDITS: dict[str, int] = {
    plan: _coerce_plan_credit_limit(config.get("credits", 0))
    for plan, config in PLANS.items()
}

GRACE_PERIOD_DAYS = 7


def _coerce_rowcount(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        rowcount = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return rowcount if rowcount > 0 else 0


@celery_app.task(
    name="codey.saas.tasks.billing.reset_monthly_credits",
    bind=True,
)
def reset_monthly_credits(self) -> dict:
    """Reset credits on the 1st of each month for all active subscribers.

    Runs daily but only performs resets when ``day == 1``.
    """
    now = datetime.utcnow()
    if now.day != 1:
        return {"status": "skipped", "reason": "not first of month"}

    async def _reset() -> dict:
        from codey.saas.database import async_session_factory
        from sqlalchemy import text

        async with async_session_factory() as db:
            total_updated = 0
            for plan, credits in PLAN_MONTHLY_CREDITS.items():
                res = await db.execute(
                    text(
                        "UPDATE users "
                        "SET credits_remaining = :credits, "
                        "    credits_used_this_month = 0 "
                        "WHERE plan = :plan AND plan_status = 'active'"
                    ),
                    {"credits": credits, "plan": plan},
                )
                total_updated += _coerce_rowcount(res.rowcount)

            await db.commit()
            logger.info("Monthly credit reset: updated %d users", total_updated)
            return {"status": "completed", "users_updated": total_updated}

    return run_sync_task(_reset())


@celery_app.task(
    name="codey.saas.tasks.billing.check_grace_period",
    bind=True,
)
def check_grace_period(self) -> dict:
    """Downgrade users whose subscription lapsed beyond the grace period."""
    async def _check() -> dict:
        from codey.saas.database import async_session_factory
        from sqlalchemy import text

        grace_cutoff = datetime.utcnow() - timedelta(days=GRACE_PERIOD_DAYS)

        async with async_session_factory() as db:
            result = await db.execute(
                text(
                    "UPDATE users "
                    "SET plan = 'free', "
                    "    plan_status = 'expired', "
                    "    credits_remaining = LEAST(credits_remaining, :free_credits) "
                    "WHERE plan_status = 'past_due' "
                    "AND subscription_period_end < :grace_cutoff"
                ),
                {
                    "free_credits": PLAN_MONTHLY_CREDITS["free"],
                    "grace_cutoff": grace_cutoff,
                },
            )
            downgraded = _coerce_rowcount(result.rowcount)
            await db.commit()

            if downgraded:
                logger.info("Grace period expired: downgraded %d users", downgraded)
            return {"status": "completed", "downgraded": downgraded}

    return run_sync_task(_check())
