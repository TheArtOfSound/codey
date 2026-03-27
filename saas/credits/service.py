from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.models import CreditTransaction, User

# ---------------------------------------------------------------------------
# Credit costs per action
# ---------------------------------------------------------------------------
CREDIT_COSTS: dict[str, int] = {
    "simple_prompt": 1,       # < 50 lines
    "medium_prompt": 3,       # 50-200 lines
    "large_prompt": 8,        # 200-500 lines
    "full_build": 20,         # 500+ lines
    "file_analysis": 2,       # Upload + NFET analysis
    "structural_refactor": 5, # NFET-guided refactoring
    "autonomous_daily": 10,   # Per day of autonomous mode
    "github_commit": 1,       # Each automated commit/PR
    "test_generation": 2,     # Test suite for a module
}

# ---------------------------------------------------------------------------
# Monthly credits per plan
# ---------------------------------------------------------------------------
PLAN_CREDITS: dict[str, int] = {
    "free": 10,
    "starter": 100,
    "pro": 400,
    "team": 1500,
}

# ---------------------------------------------------------------------------
# Rollover limits per plan
# ---------------------------------------------------------------------------
PLAN_ROLLOVER: dict[str, int] = {
    "free": 0,
    "starter": 50,
    "pro": 200,
    "team": 750,
}


class InsufficientCreditsError(Exception):
    """Raised when a user does not have enough credits for an action."""

    def __init__(self, required: int, available: int) -> None:
        self.required = required
        self.available = available
        super().__init__(
            f"Insufficient credits: {required} required, {available} available"
        )


class CreditService:
    """Core credit management — the most critical business logic in Codey SaaS.

    Every credit mutation flows through this service so that balances stay
    consistent and every change is audit-logged via ``CreditTransaction``.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    async def _get_user(self, user_id: UUID, *, lock: bool = False) -> User:
        """Fetch a user row, optionally with SELECT … FOR UPDATE."""
        stmt = select(User).where(User.id == user_id)
        if lock:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        user = result.scalar_one_or_none()
        if user is None:
            raise ValueError(f"User {user_id} not found")
        return user

    async def _log_transaction(
        self,
        *,
        user_id: UUID,
        amount: int,
        tx_type: str,
        description: str,
        credits_before: int,
        credits_after: int,
        session_id: UUID | None = None,
        stripe_payment_intent_id: str | None = None,
    ) -> CreditTransaction:
        tx = CreditTransaction(
            user_id=user_id,
            amount=amount,
            type=tx_type,
            description=description,
            credits_before=credits_before,
            credits_after=credits_after,
            session_id=session_id,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
        self.db.add(tx)
        await self.db.flush()
        return tx

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_credits(self, user_id: UUID, estimated_cost: int) -> bool:
        """Return *True* if the user can afford ``estimated_cost`` credits."""
        user = await self._get_user(user_id)
        total = user.credits_remaining + user.topup_credits
        return total >= estimated_cost

    async def reserve_credits(
        self,
        user_id: UUID,
        estimated_cost: int,
        description: str,
        session_id: UUID | None = None,
    ) -> CreditTransaction:
        """Atomically reserve credits before a session starts.

        Uses ``SELECT … FOR UPDATE`` to lock the user row and prevent race
        conditions.  Subscription credits are consumed first; topup credits
        cover any remainder.
        """
        user = await self._get_user(user_id, lock=True)

        total_available = user.credits_remaining + user.topup_credits
        if total_available < estimated_cost:
            raise InsufficientCreditsError(
                required=estimated_cost, available=total_available
            )

        credits_before = total_available

        # Deduct from subscription credits first, then topup
        remaining_cost = estimated_cost
        if user.credits_remaining >= remaining_cost:
            user.credits_remaining -= remaining_cost
        else:
            remaining_cost -= user.credits_remaining
            user.credits_remaining = 0
            user.topup_credits -= remaining_cost

        user.credits_used_this_month += estimated_cost

        credits_after = user.credits_remaining + user.topup_credits

        tx = await self._log_transaction(
            user_id=user_id,
            amount=-estimated_cost,
            tx_type="session_charge",
            description=description,
            credits_before=credits_before,
            credits_after=credits_after,
            session_id=session_id,
        )

        await self.db.flush()
        return tx

    async def refund_credits(
        self,
        user_id: UUID,
        amount: int,
        description: str,
        session_id: UUID | None = None,
    ) -> CreditTransaction:
        """Refund credits back to the user's subscription balance."""
        user = await self._get_user(user_id, lock=True)

        credits_before = user.credits_remaining + user.topup_credits

        user.credits_remaining += amount
        if user.credits_used_this_month >= amount:
            user.credits_used_this_month -= amount
        else:
            user.credits_used_this_month = 0

        credits_after = user.credits_remaining + user.topup_credits

        tx = await self._log_transaction(
            user_id=user_id,
            amount=amount,
            tx_type="refund",
            description=description,
            credits_before=credits_before,
            credits_after=credits_after,
            session_id=session_id,
        )

        await self.db.flush()
        return tx

    async def add_monthly_credits(self, user_id: UUID) -> CreditTransaction:
        """Apply monthly credit reset with rollover on subscription renewal."""
        user = await self._get_user(user_id, lock=True)

        plan = user.plan
        credits_before = user.credits_remaining + user.topup_credits

        max_rollover = PLAN_ROLLOVER.get(plan, 0)
        rollover = min(user.credits_remaining, max_rollover)
        monthly_allocation = PLAN_CREDITS.get(plan, 0)

        user.credits_remaining = monthly_allocation + rollover
        user.credits_used_this_month = 0

        credits_after = user.credits_remaining + user.topup_credits

        tx = await self._log_transaction(
            user_id=user_id,
            amount=monthly_allocation + rollover,
            tx_type="monthly_reset",
            description=(
                f"Monthly reset for {plan} plan: "
                f"{monthly_allocation} allocated + {rollover} rolled over"
            ),
            credits_before=credits_before,
            credits_after=credits_after,
        )

        await self.db.flush()
        return tx

    async def add_topup_credits(
        self,
        user_id: UUID,
        amount: int,
        stripe_payment_intent_id: str,
    ) -> CreditTransaction:
        """Add purchased top-up credits (separate from subscription balance)."""
        user = await self._get_user(user_id, lock=True)

        credits_before = user.credits_remaining + user.topup_credits
        user.topup_credits += amount
        credits_after = user.credits_remaining + user.topup_credits

        tx = await self._log_transaction(
            user_id=user_id,
            amount=amount,
            tx_type="topup_purchase",
            description=f"Purchased {amount} top-up credits",
            credits_before=credits_before,
            credits_after=credits_after,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )

        await self.db.flush()
        return tx

    async def adjust_credits(
        self,
        user_id: UUID,
        amount: int,
        description: str,
    ) -> CreditTransaction:
        """Admin adjustment — positive adds credits, negative removes them."""
        user = await self._get_user(user_id, lock=True)

        credits_before = user.credits_remaining + user.topup_credits
        user.credits_remaining += amount
        credits_after = user.credits_remaining + user.topup_credits

        tx = await self._log_transaction(
            user_id=user_id,
            amount=amount,
            tx_type="admin_adjustment",
            description=description,
            credits_before=credits_before,
            credits_after=credits_after,
        )

        await self.db.flush()
        return tx

    async def get_balance(self, user_id: UUID) -> dict:
        """Return the user's full credit balance breakdown."""
        user = await self._get_user(user_id)
        plan = user.plan
        return {
            "subscription_credits": user.credits_remaining,
            "topup_credits": user.topup_credits,
            "total": user.credits_remaining + user.topup_credits,
            "used_this_month": user.credits_used_this_month,
            "plan": plan,
            "monthly_allocation": PLAN_CREDITS.get(plan, 0),
        }

    async def get_transaction_history(
        self,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Return paginated transaction history, most recent first."""
        stmt = (
            select(CreditTransaction)
            .where(CreditTransaction.user_id == user_id)
            .order_by(CreditTransaction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        rows = result.scalars().all()
        return [
            {
                "id": str(tx.id),
                "amount": tx.amount,
                "type": tx.type,
                "description": tx.description,
                "credits_before": tx.credits_before,
                "credits_after": tx.credits_after,
                "session_id": str(tx.session_id) if tx.session_id else None,
                "created_at": tx.created_at.isoformat(),
            }
            for tx in rows
        ]

    async def check_low_credits_warning(self, user_id: UUID) -> bool:
        """Return *True* if the user's credits are below 20% of their plan."""
        user = await self._get_user(user_id)
        monthly = PLAN_CREDITS.get(user.plan, 0)
        if monthly == 0:
            return False
        total = user.credits_remaining + user.topup_credits
        return total < (monthly * 0.20)

    @staticmethod
    def estimate_cost(prompt: str, mode: str) -> int:
        """Estimate credit cost from prompt length and execution mode."""
        if mode == "analyze":
            return CREDIT_COSTS["file_analysis"]
        if mode == "autonomous":
            return CREDIT_COSTS["autonomous_daily"]

        line_count = prompt.count("\n") + 1
        if line_count < 50:
            return CREDIT_COSTS["simple_prompt"]
        if line_count < 200:
            return CREDIT_COSTS["medium_prompt"]
        if line_count < 500:
            return CREDIT_COSTS["large_prompt"]
        return CREDIT_COSTS["full_build"]
