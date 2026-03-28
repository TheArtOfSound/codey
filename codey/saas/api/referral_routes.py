"""Referral system API — link generation, stats, and conversion tracking."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.config import settings
from codey.saas.database import get_db
from codey.saas.models.credit_transaction import CreditTransaction
from codey.saas.models.referral import Referral
from codey.saas.models.user import User

router = APIRouter(prefix="/referrals", tags=["referrals"])

REFERRER_CREDITS = 5
REFERRED_CREDITS = 3


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ReferralStatsResponse(BaseModel):
    referral_link: str
    total_referrals: int
    pending: int
    converted: int
    total_credits_earned: int


class ConvertRequest(BaseModel):
    referrer_id: str
    referred_id: str


class ConvertResponse(BaseModel):
    referral_id: str
    referrer_credits_issued: int
    referred_credits_issued: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ReferralStatsResponse)
async def get_referral_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReferralStatsResponse:
    """Get the current user's referral stats and shareable link."""
    user_id = current_user.id

    # Build referral link using user id
    referral_link = f"{settings.frontend_url}/signup?ref={user_id}"

    # Count totals
    total_result = await db.execute(
        select(func.count(Referral.id)).where(Referral.referrer_id == user_id)
    )
    total_referrals = total_result.scalar_one()

    pending_result = await db.execute(
        select(func.count(Referral.id))
        .where(Referral.referrer_id == user_id)
        .where(Referral.status == "pending")
    )
    pending = pending_result.scalar_one()

    converted_result = await db.execute(
        select(func.count(Referral.id))
        .where(Referral.referrer_id == user_id)
        .where(Referral.status == "converted")
    )
    converted = converted_result.scalar_one()

    credits_result = await db.execute(
        select(func.coalesce(func.sum(Referral.credits_issued_referrer), 0))
        .where(Referral.referrer_id == user_id)
    )
    total_credits_earned = credits_result.scalar_one()

    return ReferralStatsResponse(
        referral_link=referral_link,
        total_referrals=total_referrals,
        pending=pending,
        converted=converted,
        total_credits_earned=total_credits_earned,
    )


@router.post("/convert", response_model=ConvertResponse)
async def convert_referral(
    body: ConvertRequest,
    db: AsyncSession = Depends(get_db),
) -> ConvertResponse:
    """Mark a referral as converted and issue credits to both parties.

    Called internally when a referred user upgrades to a paid plan.
    """
    referrer_id = uuid.UUID(body.referrer_id)
    referred_id = uuid.UUID(body.referred_id)

    if referrer_id == referred_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot refer yourself",
        )

    # Check for existing referral
    result = await db.execute(
        select(Referral)
        .where(Referral.referrer_id == referrer_id)
        .where(Referral.referred_id == referred_id)
    )
    referral = result.scalar_one_or_none()

    if referral is None:
        # Create the referral record on the fly if it doesn't exist yet
        referral = Referral(
            referrer_id=referrer_id,
            referred_id=referred_id,
            status="pending",
        )
        db.add(referral)
        await db.flush()

    if referral.status == "converted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Referral already converted",
        )

    # Mark as converted
    referral.status = "converted"
    referral.converted_at = datetime.utcnow()
    referral.credits_issued_referrer = REFERRER_CREDITS
    referral.credits_issued_referred = REFERRED_CREDITS

    # Issue credits to referrer
    referrer = await db.get(User, referrer_id)
    if referrer is not None:
        referrer.topup_credits += REFERRER_CREDITS
        tx_referrer = CreditTransaction(
            user_id=referrer_id,
            amount=REFERRER_CREDITS,
            type="referral_bonus",
            description=f"Referral bonus: {referred_id} upgraded",
            credits_before=referrer.topup_credits - REFERRER_CREDITS,
            credits_after=referrer.topup_credits,
        )
        db.add(tx_referrer)

    # Issue credits to referred user
    referred = await db.get(User, referred_id)
    if referred is not None:
        referred.topup_credits += REFERRED_CREDITS
        tx_referred = CreditTransaction(
            user_id=referred_id,
            amount=REFERRED_CREDITS,
            type="referral_welcome",
            description=f"Welcome bonus from referral by {referrer_id}",
            credits_before=referred.topup_credits - REFERRED_CREDITS,
            credits_after=referred.topup_credits,
        )
        db.add(tx_referred)

    await db.flush()

    return ConvertResponse(
        referral_id=str(referral.id),
        referrer_credits_issued=REFERRER_CREDITS,
        referred_credits_issued=REFERRED_CREDITS,
    )
