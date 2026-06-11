from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.billing.plans import PLANS
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
    plan: int(config.get("credits", 0))
    for plan, config in PLANS.items()
}

# ---------------------------------------------------------------------------
# Rollover limits per plan
# ---------------------------------------------------------------------------
PLAN_ROLLOVER: dict[str, int] = {
    plan: int(config.get("rollover", 0))
    for plan, config in PLANS.items()
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

    @staticmethod
    def _serialize_credit_timestamp(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return str(value)

    @staticmethod
    def _coerce_plan_name(value: object) -> str:
        if not isinstance(value, str):
            return "free"
        normalized = value.strip().lower()
        return normalized or "free"

    @staticmethod
    def _coerce_credit_text(value: object, default: str | None = None) -> str | None:
        if not isinstance(value, str):
            return default
        normalized = value.strip()
        if not normalized:
            return default
        return normalized

    @staticmethod
    def _coerce_credit_identifier(value: object) -> str | None:
        if value is None or isinstance(value, bool):
            return None
        if not isinstance(value, (str, int, UUID)):
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _coerce_optional_credit_int(value: object) -> int | None:
        if value is None:
            return None
        return User._coerce_credit_value(value)

    @staticmethod
    def _coerce_positive_credit_amount(value: object, field_name: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{field_name} must be a positive integer")
        try:
            amount = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{field_name} must be a positive integer") from exc
        if amount <= 0:
            raise ValueError(f"{field_name} must be a positive integer")
        return amount

    @staticmethod
    def _coerce_credit_row_list(value: object) -> list[object]:
        if isinstance(value, (list, tuple)):
            return list(value)
        return []

    @classmethod
    def _normalize_user_credit_state(cls, user: User) -> tuple[str, int, int, int]:
        plan = cls._coerce_plan_name(getattr(user, "plan", None))
        credits_remaining = User._coerce_credit_value(
            getattr(user, "credits_remaining", None)
        )
        topup_credits = User._coerce_credit_value(getattr(user, "topup_credits", None))
        used_this_month = User._coerce_credit_value(
            getattr(user, "credits_used_this_month", None)
        )

        user.plan = plan
        user.credits_remaining = credits_remaining
        user.topup_credits = topup_credits
        user.credits_used_this_month = used_this_month

        return plan, credits_remaining, topup_credits, used_this_month

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

    async def _get_transaction_by_payment_intent(
        self,
        stripe_payment_intent_id: str,
    ) -> CreditTransaction | None:
        result = await self.db.execute(
            select(CreditTransaction).where(
                CreditTransaction.stripe_payment_intent_id == stripe_payment_intent_id
            )
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_credits(self, user_id: UUID, estimated_cost: int) -> bool:
        """Return *True* if the user can afford ``estimated_cost`` credits."""
        user = await self._get_user(user_id)
        _, credits_remaining, topup_credits, _ = self._normalize_user_credit_state(user)
        total = credits_remaining + topup_credits
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
        estimated_cost = self._coerce_positive_credit_amount(
            estimated_cost,
            "estimated_cost",
        )
        user = await self._get_user(user_id, lock=True)
        plan, credits_remaining, topup_credits, used_this_month = (
            self._normalize_user_credit_state(user)
        )

        total_available = credits_remaining + topup_credits
        if total_available < estimated_cost:
            raise InsufficientCreditsError(
                required=estimated_cost, available=total_available
            )

        credits_before = total_available

        # Deduct from subscription credits first, then topup
        remaining_cost = estimated_cost
        if credits_remaining >= remaining_cost:
            user.credits_remaining = credits_remaining - remaining_cost
        else:
            remaining_cost -= credits_remaining
            user.credits_remaining = 0
            user.topup_credits = topup_credits - remaining_cost

        user.credits_used_this_month = used_this_month + estimated_cost

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

        # Check if credits dropped below 20% — send low credits email
        plan_credits = PLAN_CREDITS.get(plan, 10)
        if plan_credits > 0 and credits_after > 0:
            pct = credits_after / plan_credits
            if pct <= 0.20 and credits_before / plan_credits > 0.20:
                # Just crossed the 20% threshold
                try:
                    from codey.saas.emails.service import EmailService

                    email_svc = EmailService()
                    await email_svc.send_low_credits(
                        user.email,
                        remaining=credits_after,
                        monthly=plan_credits,
                    )
                except Exception:
                    pass  # Email is best-effort

        return tx

    async def refund_credits(
        self,
        user_id: UUID,
        amount: int,
        description: str,
        session_id: UUID | None = None,
    ) -> CreditTransaction:
        """Refund credits back to the user's subscription balance."""
        amount = self._coerce_positive_credit_amount(amount, "amount")
        user = await self._get_user(user_id, lock=True)
        _, credits_remaining, topup_credits, used_this_month = (
            self._normalize_user_credit_state(user)
        )

        credits_before = credits_remaining + topup_credits

        user.credits_remaining = credits_remaining + amount
        if used_this_month >= amount:
            user.credits_used_this_month = used_this_month - amount
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
        plan, credits_remaining, topup_credits, _ = self._normalize_user_credit_state(
            user
        )

        credits_before = credits_remaining + topup_credits

        max_rollover = PLAN_ROLLOVER.get(plan, 0)
        rollover = min(credits_remaining, max_rollover)
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
        amount = self._coerce_positive_credit_amount(amount, "amount")
        payment_intent_id = self._coerce_credit_text(stripe_payment_intent_id)
        if payment_intent_id is None:
            raise ValueError("stripe_payment_intent_id is required for top-up credits")

        existing_tx = await self._get_transaction_by_payment_intent(payment_intent_id)
        if existing_tx is not None:
            return existing_tx

        user = await self._get_user(user_id, lock=True)
        existing_tx = await self._get_transaction_by_payment_intent(payment_intent_id)
        if existing_tx is not None:
            return existing_tx

        _, credits_remaining, topup_credits, _ = self._normalize_user_credit_state(user)

        credits_before = credits_remaining + topup_credits
        user.topup_credits = topup_credits + amount
        credits_after = user.credits_remaining + user.topup_credits

        tx = await self._log_transaction(
            user_id=user_id,
            amount=amount,
            tx_type="topup_purchase",
            description=f"Purchased {amount} top-up credits",
            credits_before=credits_before,
            credits_after=credits_after,
            stripe_payment_intent_id=payment_intent_id,
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
        _, credits_remaining, topup_credits, _ = self._normalize_user_credit_state(user)

        credits_before = credits_remaining + topup_credits
        user.credits_remaining = max(0, credits_remaining + amount)
        applied_amount = user.credits_remaining - credits_remaining
        credits_after = user.credits_remaining + user.topup_credits

        tx = await self._log_transaction(
            user_id=user_id,
            amount=applied_amount,
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
        plan, subscription_credits, topup_credits, used_this_month = (
            self._normalize_user_credit_state(user)
        )
        return {
            "subscription_credits": subscription_credits,
            "topup_credits": topup_credits,
            "total": subscription_credits + topup_credits,
            "used_this_month": used_this_month,
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
        rows = self._coerce_credit_row_list(result.scalars().all())
        return [
            {
                "id": self._coerce_credit_identifier(getattr(tx, "id", None)) or "",
                "amount": User._coerce_credit_value(getattr(tx, "amount", None)),
                "type": self._coerce_credit_text(
                    getattr(tx, "type", None),
                    "unknown",
                ),
                "description": self._coerce_credit_text(
                    getattr(tx, "description", None)
                ),
                "credits_before": self._coerce_optional_credit_int(
                    getattr(tx, "credits_before", None)
                ),
                "credits_after": self._coerce_optional_credit_int(
                    getattr(tx, "credits_after", None)
                ),
                "session_id": self._coerce_credit_identifier(
                    getattr(tx, "session_id", None)
                ),
                "created_at": self._serialize_credit_timestamp(
                    getattr(tx, "created_at", None)
                ) or "",
            }
            for tx in rows
        ]

    async def check_low_credits_warning(self, user_id: UUID) -> bool:
        """Return *True* if the user's credits are below 20% of their plan."""
        user = await self._get_user(user_id)
        plan, credits_remaining, topup_credits, _ = self._normalize_user_credit_state(
            user
        )
        monthly = PLAN_CREDITS.get(plan, 0)
        if monthly == 0:
            return False
        total = credits_remaining + topup_credits
        return total < (monthly * 0.20)

    @staticmethod
    def estimate_cost(prompt: str, mode: str) -> int:
        """Estimate credit cost from prompt length and execution mode."""
        if mode == "analyze":
            return CREDIT_COSTS["file_analysis"]
        if mode == "autonomous":
            return CREDIT_COSTS["autonomous_daily"]

        line_count = sum(1 for line in prompt.splitlines() if line.strip())
        if line_count < 50:
            return CREDIT_COSTS["simple_prompt"]
        if line_count < 200:
            return CREDIT_COSTS["medium_prompt"]
        if line_count < 500:
            return CREDIT_COSTS["large_prompt"]
        return CREDIT_COSTS["full_build"]
