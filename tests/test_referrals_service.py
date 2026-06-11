from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

import codey.saas.referrals as referrals


class _ReferralResult:
    def __init__(self, referral) -> None:
        self._referral = referral

    def scalars(self):
        return self

    def first(self):
        return self._referral


class _ReferralDB:
    def __init__(self, referral, users) -> None:
        self._referral = referral
        self._users = users
        self.added: list[object] = []
        self.flush_calls = 0

    async def execute(self, _statement):
        return _ReferralResult(self._referral)

    async def get(self, _model, user_id):
        return self._users.get(user_id)

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_calls += 1


@pytest.mark.asyncio
async def test_convert_pending_referral_coerces_legacy_topup_credit_fields() -> None:
    referrer_id = uuid4()
    referred_id = uuid4()
    referral = SimpleNamespace(
        referrer_id=referrer_id,
        referred_id=referred_id,
        status="pending",
        created_at=datetime.utcnow(),
        converted_at=None,
        credits_issued_referrer=None,
        credits_issued_referred=None,
    )
    referrer = SimpleNamespace(id=referrer_id, topup_credits="7")
    referred = SimpleNamespace(id=referred_id, topup_credits={"value": 2})
    db = _ReferralDB(
        referral,
        {
            referrer_id: referrer,
            referred_id: referred,
        },
    )

    result = await referrals.convert_pending_referral(
        db,
        referred_id=referred_id,
    )

    assert result is referral
    assert referral.status == "converted"
    assert referral.credits_issued_referrer == referrals.REFERRER_CREDITS
    assert referral.credits_issued_referred == referrals.REFERRED_CREDITS
    assert referrer.topup_credits == 12
    assert referred.topup_credits == 3
    assert len(db.added) == 2
    assert db.added[0].credits_before == 7
    assert db.added[0].credits_after == 12
    assert db.added[1].credits_before == 0
    assert db.added[1].credits_after == 3
    assert db.flush_calls == 1
