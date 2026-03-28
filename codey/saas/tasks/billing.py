from __future__ import annotations

import logging
from datetime import datetime, timezone

from codey.saas.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plan credit limits (mirrored from billing config)
# ---------------------------------------------------------------------------
PLAN_MONTHLY_CREDITS: dict[str, int] = {
    "free": 10,
    "starter": 100,
    "pro": 500,
    "team": 2000,
}

GRACE_PERIOD_DAYS = 7


@celery_app.task(
    name="codey.saas.tasks.billing.reset_monthly_credits",
    bind=True,
)
def reset_monthly_credits(self) -> dict:
    """Reset credits on the 1st of each month for all active subscribers.

    Runs daily but only performs resets when ``day == 1``.
    """
    import asyncio

    now = datetime.now(timezone.utc)
    if now.day != 1:
        return {"status": "skipped", "reason": "not first of month"}

    async def _reset() -> dict:
        from codey.saas.database import async_session_factory
        from sqlalchemy import text

        async with async_session_factory() as db:
            result = await db.execute(
                text(
                    "UPDATE users "
                    "SET credits_remaining = :credits, credits_used_this_month = 0 "
                    "WHERE plan = :plan AND plan_status = 'active'"
                ),
                [
                    {"credits": credits, "plan": plan}
                    for plan, credits in PLAN_MONTHLY_CREDITS.items()
                ],
            )

            # Batch update — one statement per plan for clarity
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
                total_updated += res.rowcount  # type: ignore[union-attr]

            await db.commit()
            logger.info("Monthly credit reset: updated %d users", total_updated)
            return {"status": "completed", "users_updated": total_updated}

    return asyncio.get_event_loop().run_until_complete(_reset())


@celery_app.task(
    name="codey.saas.tasks.billing.check_grace_period",
    bind=True,
)
def check_grace_period(self) -> dict:
    """Downgrade users whose subscription lapsed beyond the grace period."""
    import asyncio

    async def _check() -> dict:
        from codey.saas.database import async_session_factory
        from sqlalchemy import text

        async with async_session_factory() as db:
            result = await db.execute(
                text(
                    "UPDATE users "
                    "SET plan = 'free', "
                    "    plan_status = 'expired', "
                    "    credits_remaining = LEAST(credits_remaining, :free_credits) "
                    "WHERE plan_status = 'past_due' "
                    "AND subscription_period_end < now() - INTERVAL ':days days'"
                ),
                {
                    "free_credits": PLAN_MONTHLY_CREDITS["free"],
                    "days": GRACE_PERIOD_DAYS,
                },
            )
            downgraded = result.rowcount  # type: ignore[union-attr]
            await db.commit()

            if downgraded:
                logger.info("Grace period expired: downgraded %d users", downgraded)
            return {"status": "completed", "downgraded": downgraded}

    return asyncio.get_event_loop().run_until_complete(_check())
