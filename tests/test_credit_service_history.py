from __future__ import annotations

import sys
import types
import uuid
from types import SimpleNamespace

import pytest

from codey.saas.billing.plans import PLANS
from codey.saas.credits.service import CreditService


class _HistoryResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _HistoryDB:
    def __init__(self, rows) -> None:
        self._rows = rows
        self.execute_calls = 0

    async def execute(self, _statement):
        self.execute_calls += 1
        return _HistoryResult(self._rows)


class _FlushDB:
    def __init__(self) -> None:
        self.flush_calls = 0

    async def flush(self) -> None:
        self.flush_calls += 1


def test_credit_plan_tables_match_billing_plan_config() -> None:
    from codey.saas.credits.service import PLAN_CREDITS, PLAN_ROLLOVER

    assert PLAN_CREDITS == {
        plan: int(config.get("credits", 0))
        for plan, config in PLANS.items()
    }
    assert PLAN_ROLLOVER == {
        plan: int(config.get("rollover", 0))
        for plan, config in PLANS.items()
    }


def test_coerce_positive_credit_amount_rejects_non_positive_values() -> None:
    assert CreditService._coerce_positive_credit_amount("3", "amount") == 3

    with pytest.raises(ValueError, match="amount"):
        CreditService._coerce_positive_credit_amount(0, "amount")
    with pytest.raises(ValueError, match="amount"):
        CreditService._coerce_positive_credit_amount(-1, "amount")
    with pytest.raises(ValueError, match="amount"):
        CreditService._coerce_positive_credit_amount(True, "amount")


def test_estimate_cost_ignores_blank_and_trailing_prompt_lines() -> None:
    prompt = "print('ok')" + ("\n" * 100)

    assert CreditService.estimate_cost(prompt, "prompt") == 1


@pytest.mark.asyncio
async def test_get_transaction_history_tolerates_string_created_at() -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        amount=5,
        type="usage",
        description="Autonomous run",
        credits_before=20,
        credits_after=15,
        session_id=session_id,
        created_at=" 2026-01-02T03:04:05Z ",
    )

    service = CreditService(_HistoryDB([row]))

    history = await service.get_transaction_history(user_id)

    assert len(history) == 1
    assert history[0]["created_at"] == "2026-01-02T03:04:05Z"
    assert history[0]["session_id"] == str(session_id)


@pytest.mark.asyncio
async def test_get_balance_coerces_legacy_credit_fields() -> None:
    user_id = uuid.uuid4()
    user = SimpleNamespace(
        credits_remaining="10",
        topup_credits={"value": 5},
        credits_used_this_month="3",
        plan=["pro"],
    )

    service = CreditService(SimpleNamespace())

    async def fake_get_user(_user_id, *, lock=False):
        assert _user_id == user_id
        assert lock is False
        return user

    service._get_user = fake_get_user

    balance = await service.get_balance(user_id)

    assert balance == {
        "subscription_credits": 10,
        "topup_credits": 0,
        "total": 10,
        "used_this_month": 3,
        "plan": "free",
        "monthly_allocation": 10,
    }


@pytest.mark.asyncio
async def test_get_balance_tolerates_missing_legacy_credit_fields() -> None:
    user_id = uuid.uuid4()
    user = SimpleNamespace()

    service = CreditService(SimpleNamespace())

    async def fake_get_user(_user_id, *, lock=False):
        assert _user_id == user_id
        assert lock is False
        return user

    service._get_user = fake_get_user

    balance = await service.get_balance(user_id)

    assert balance == {
        "subscription_credits": 0,
        "topup_credits": 0,
        "total": 0,
        "used_this_month": 0,
        "plan": "free",
        "monthly_allocation": 10,
    }
    assert user.plan == "free"
    assert user.credits_remaining == 0
    assert user.topup_credits == 0
    assert user.credits_used_this_month == 0


@pytest.mark.asyncio
async def test_get_transaction_history_coerces_malformed_fields() -> None:
    user_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        amount="5",
        type=["usage"],
        description={"text": "Autonomous run"},
        credits_before="20",
        credits_after={"value": 15},
        session_id=["session-1"],
        created_at="2026-01-02T03:04:05Z",
    )

    service = CreditService(_HistoryDB([row]))

    history = await service.get_transaction_history(user_id)

    assert history == [
        {
            "id": str(row.id),
            "amount": 5,
            "type": "unknown",
            "description": None,
            "credits_before": 20,
            "credits_after": 0,
            "session_id": None,
            "created_at": "2026-01-02T03:04:05Z",
        }
    ]


@pytest.mark.asyncio
async def test_get_transaction_history_tolerates_missing_legacy_fields() -> None:
    user_id = uuid.uuid4()
    row = SimpleNamespace(id=uuid.uuid4())

    service = CreditService(_HistoryDB([row]))

    history = await service.get_transaction_history(user_id)

    assert history == [
        {
            "id": str(row.id),
            "amount": 0,
            "type": "unknown",
            "description": None,
            "credits_before": None,
            "credits_after": None,
            "session_id": None,
            "created_at": "",
        }
    ]


@pytest.mark.asyncio
async def test_get_transaction_history_tolerates_malformed_result_rows() -> None:
    user_id = uuid.uuid4()
    service = CreditService(_HistoryDB(None))

    history = await service.get_transaction_history(user_id)

    assert history == []


@pytest.mark.asyncio
async def test_reserve_credits_coerces_legacy_credit_fields() -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user = SimpleNamespace(
        credits_remaining="5",
        topup_credits="3",
        credits_used_this_month="4",
        plan=["pro"],
        email="user@example.com",
    )
    db = _FlushDB()
    service = CreditService(db)

    async def fake_get_user(_user_id, *, lock=False):
        assert _user_id == user_id
        assert lock is True
        return user

    captured: dict[str, object] = {}

    async def fake_log_transaction(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="tx-1")

    service._get_user = fake_get_user
    service._log_transaction = fake_log_transaction

    tx = await service.reserve_credits(
        user_id=user_id,
        estimated_cost=8,
        description="Run session",
        session_id=session_id,
    )

    assert tx.id == "tx-1"
    assert user.plan == "free"
    assert user.credits_remaining == 0
    assert user.topup_credits == 0
    assert user.credits_used_this_month == 12
    assert captured["credits_before"] == 8
    assert captured["credits_after"] == 0
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_reserve_credits_rejects_non_positive_cost_before_locking_user() -> None:
    service = CreditService(_FlushDB())

    async def fail_get_user(*_args, **_kwargs):
        raise AssertionError("invalid costs should fail before user lookup")

    service._get_user = fail_get_user

    with pytest.raises(ValueError, match="estimated_cost"):
        await service.reserve_credits(
            user_id=uuid.uuid4(),
            estimated_cost=-1,
            description="Invalid run",
        )


@pytest.mark.asyncio
async def test_reserve_credits_sends_low_credit_warning_when_threshold_crossed(
    monkeypatch,
) -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user = SimpleNamespace(
        credits_remaining=3,
        topup_credits=0,
        credits_used_this_month=0,
        plan="free",
        email="user@example.com",
    )
    db = _FlushDB()
    service = CreditService(db)

    async def fake_get_user(_user_id, *, lock=False):
        assert _user_id == user_id
        assert lock is True
        return user

    async def fake_log_transaction(**kwargs):
        return SimpleNamespace(id="tx-low-credit", **kwargs)

    captured: dict[str, object] = {}

    class _FakeEmailService:
        async def send_low_credits(
            self,
            email: str,
            remaining: int,
            monthly: int,
        ) -> bool:
            captured["email"] = email
            captured["remaining"] = remaining
            captured["monthly"] = monthly
            return True

    monkeypatch.setitem(
        sys.modules,
        "codey.saas.emails.service",
        types.SimpleNamespace(EmailService=_FakeEmailService),
    )
    service._get_user = fake_get_user
    service._log_transaction = fake_log_transaction

    tx = await service.reserve_credits(
        user_id=user_id,
        estimated_cost=1,
        description="Small run",
        session_id=session_id,
    )

    assert tx.id == "tx-low-credit"
    assert user.credits_remaining == 2
    assert user.topup_credits == 0
    assert captured == {
        "email": "user@example.com",
        "remaining": 2,
        "monthly": 10,
    }
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_add_topup_credits_is_idempotent_for_existing_payment_intent() -> None:
    user_id = uuid.uuid4()
    existing_tx = SimpleNamespace(id="tx-existing")
    db = _HistoryDB([existing_tx])
    service = CreditService(db)

    async def fail_get_user(*_args, **_kwargs):
        raise AssertionError("duplicate top-up should not refetch or mutate the user")

    service._get_user = fail_get_user

    tx = await service.add_topup_credits(
        user_id=user_id,
        amount=50,
        stripe_payment_intent_id=" pi_123 ",
    )

    assert tx is existing_tx
    assert db.execute_calls == 1


@pytest.mark.asyncio
async def test_add_topup_credits_rechecks_idempotency_after_user_lock() -> None:
    user_id = uuid.uuid4()
    user = SimpleNamespace(
        credits_remaining=10,
        topup_credits=0,
        credits_used_this_month=0,
        plan="free",
    )
    existing_tx = SimpleNamespace(id="tx-existing-after-lock")
    service = CreditService(SimpleNamespace())
    lookup_results = [None, existing_tx]
    lock_calls = 0

    async def fake_get_transaction_by_payment_intent(payment_intent_id: str):
        assert payment_intent_id == "pi_race"
        return lookup_results.pop(0)

    async def fake_get_user(_user_id, *, lock=False):
        nonlocal lock_calls
        assert _user_id == user_id
        assert lock is True
        lock_calls += 1
        return user

    async def fail_log_transaction(**_kwargs):
        raise AssertionError("duplicate top-up should not create another transaction")

    service._get_transaction_by_payment_intent = fake_get_transaction_by_payment_intent
    service._get_user = fake_get_user
    service._log_transaction = fail_log_transaction

    tx = await service.add_topup_credits(
        user_id=user_id,
        amount=50,
        stripe_payment_intent_id="pi_race",
    )

    assert tx is existing_tx
    assert lock_calls == 1
    assert lookup_results == []
    assert user.topup_credits == 0


@pytest.mark.asyncio
async def test_adjust_credits_coerces_legacy_credit_fields() -> None:
    user_id = uuid.uuid4()
    user = SimpleNamespace(
        credits_remaining="5",
        topup_credits="3",
        credits_used_this_month="4",
        plan=["pro"],
    )
    db = _FlushDB()
    service = CreditService(db)

    async def fake_get_user(_user_id, *, lock=False):
        assert _user_id == user_id
        assert lock is True
        return user

    captured: dict[str, object] = {}

    async def fake_log_transaction(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="tx-2")

    service._get_user = fake_get_user
    service._log_transaction = fake_log_transaction

    tx = await service.adjust_credits(
        user_id=user_id,
        amount=2,
        description="Admin adjustment",
    )

    assert tx.id == "tx-2"
    assert user.plan == "free"
    assert user.credits_remaining == 7
    assert user.topup_credits == 3
    assert captured["credits_before"] == 8
    assert captured["credits_after"] == 10
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_adjust_credits_clamps_subscription_credits_at_zero() -> None:
    user_id = uuid.uuid4()
    user = SimpleNamespace(
        credits_remaining=5,
        topup_credits=3,
        credits_used_this_month=0,
        plan="free",
    )
    db = _FlushDB()
    service = CreditService(db)

    async def fake_get_user(_user_id, *, lock=False):
        assert _user_id == user_id
        assert lock is True
        return user

    captured: dict[str, object] = {}

    async def fake_log_transaction(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="tx-clamped")

    service._get_user = fake_get_user
    service._log_transaction = fake_log_transaction

    tx = await service.adjust_credits(
        user_id=user_id,
        amount=-10,
        description="Remove credits",
    )

    assert tx.id == "tx-clamped"
    assert user.credits_remaining == 0
    assert user.topup_credits == 3
    assert captured["amount"] == -5
    assert captured["credits_before"] == 8
    assert captured["credits_after"] == 3
    assert db.flush_calls == 1
