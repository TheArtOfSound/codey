from __future__ import annotations

from codey.saas.billing.plans import PLANS
import codey.saas.tasks.billing as billing_tasks


class _FakeResult:
    def __init__(self, rowcount) -> None:
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcount) -> None:
        self.rowcount = rowcount
        self.commits = 0

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, _statement, _params=None) -> _FakeResult:
        return _FakeResult(self.rowcount)

    async def commit(self) -> None:
        self.commits += 1


def test_plan_monthly_credits_matches_billing_plan_credits() -> None:
    assert billing_tasks.PLAN_MONTHLY_CREDITS == {
        plan: int(config.get("credits", 0))
        for plan, config in PLANS.items()
    }


def test_coerce_plan_credit_limit_treats_malformed_values_as_zero() -> None:
    assert billing_tasks._coerce_plan_credit_limit(None) == 0
    assert billing_tasks._coerce_plan_credit_limit(True) == 0
    assert billing_tasks._coerce_plan_credit_limit(-10) == 0
    assert billing_tasks._coerce_plan_credit_limit("bad") == 0
    assert billing_tasks._coerce_plan_credit_limit("25") == 25


def test_coerce_rowcount_treats_unknown_counts_as_zero() -> None:
    assert billing_tasks._coerce_rowcount(None) == 0
    assert billing_tasks._coerce_rowcount(-1) == 0
    assert billing_tasks._coerce_rowcount(True) == 0
    assert billing_tasks._coerce_rowcount(float("nan")) == 0
    assert billing_tasks._coerce_rowcount(float("inf")) == 0
    assert billing_tasks._coerce_rowcount("3") == 3


def test_check_grace_period_treats_unknown_rowcount_as_zero(monkeypatch) -> None:
    session = _FakeSession(rowcount=None)

    monkeypatch.setattr(
        "codey.saas.database.async_session_factory",
        lambda: session,
    )

    result = billing_tasks.check_grace_period.run()

    assert result == {"status": "completed", "downgraded": 0}
    assert session.commits == 1
