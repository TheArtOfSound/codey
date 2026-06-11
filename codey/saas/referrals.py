from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from codey.saas.models.referral import Referral

REFERRER_CREDITS = 5
REFERRED_CREDITS = 3


async def claim_referral(
    db: AsyncSession,
    *,
    referrer_id: uuid.UUID,
    referred_id: uuid.UUID,
) -> Referral:
    from fastapi import HTTPException, status
    from sqlalchemy import select

    from codey.saas.models.referral import Referral
    from codey.saas.models.user import User

    if referrer_id == referred_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot refer yourself",
        )

    existing_result = await db.execute(
        select(Referral)
        .where(Referral.referred_id == referred_id)
        .order_by(Referral.created_at.asc())
    )
    existing = existing_result.scalars().first()
    if existing is not None:
        if existing.referrer_id == referrer_id:
            return existing
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A referral is already linked to this account",
        )

    referrer = await db.get(User, referrer_id)
    if referrer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Referrer not found",
        )

    referral = Referral(
        referrer_id=referrer_id,
        referred_id=referred_id,
        status="pending",
    )
    db.add(referral)
    await db.flush()
    return referral


async def convert_pending_referral(
    db: AsyncSession,
    *,
    referred_id: uuid.UUID,
) -> Referral | None:
    from sqlalchemy import select

    from codey.saas.models.credit_transaction import CreditTransaction
    from codey.saas.models.referral import Referral
    from codey.saas.models.user import User

    result = await db.execute(
        select(Referral)
        .where(Referral.referred_id == referred_id)
        .where(Referral.status != "converted")
        .order_by(Referral.created_at.asc())
    )
    referral = result.scalars().first()
    if referral is None:
        return None

    referral.status = "converted"
    referral.converted_at = datetime.utcnow()
    referral.credits_issued_referrer = REFERRER_CREDITS
    referral.credits_issued_referred = REFERRED_CREDITS

    referrer = await db.get(User, referral.referrer_id)
    if referrer is not None:
        referrer_topup_credits = User._coerce_credit_value(referrer.topup_credits)
        referrer.topup_credits = referrer_topup_credits + REFERRER_CREDITS
        db.add(
            CreditTransaction(
                user_id=referrer.id,
                amount=REFERRER_CREDITS,
                type="referral_bonus",
                description=f"Referral bonus: {referred_id} upgraded",
                credits_before=referrer_topup_credits,
                credits_after=referrer.topup_credits,
            )
        )

    referred = await db.get(User, referred_id)
    if referred is not None:
        referred_topup_credits = User._coerce_credit_value(referred.topup_credits)
        referred.topup_credits = referred_topup_credits + REFERRED_CREDITS
        db.add(
            CreditTransaction(
                user_id=referred.id,
                amount=REFERRED_CREDITS,
                type="referral_welcome",
                description=f"Welcome bonus from referral by {referral.referrer_id}",
                credits_before=referred_topup_credits,
                credits_after=referred.topup_credits,
            )
        )

    await db.flush()
    return referral
