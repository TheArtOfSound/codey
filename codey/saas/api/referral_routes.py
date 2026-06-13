"""Referral system API — link generation, stats, and conversion tracking."""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.auth.public_urls import get_public_frontend_origin
from codey.saas.database import get_db
from codey.saas.models.referral import Referral
from codey.saas.models.user import User
from codey.saas.referrals import (
    REFERRER_CREDITS,
    REFERRED_CREDITS,
    claim_referral,
    convert_pending_referral,
)

router = APIRouter(prefix="/referrals", tags=["referrals"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ReferralStatsResponse(BaseModel):
    referral_link: str
    total_referrals: int
    pending: int
    converted: int
    total_credits_earned: int


class ReferralEntryResponse(BaseModel):
    id: str
    email: str
    status: str
    invited_at: str
    converted_at: str | None
    credits_earned: int


class ConvertRequest(BaseModel):
    referrer_id: uuid.UUID
    referred_id: uuid.UUID


class ClaimRequest(BaseModel):
    referrer_id: uuid.UUID


class ConvertResponse(BaseModel):
    referral_id: str
    referrer_credits_issued: int
    referred_credits_issued: int


class ClaimResponse(BaseModel):
    status: str
    referral_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_referral_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    value = value.strip() if isinstance(value, str) else str(value).strip()
    if _has_ascii_control(value):
        return None
    return value or None


def _has_ascii_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _coerce_referral_text(value: object, *, fallback: str) -> str:
    if isinstance(value, str):
        value = value.strip()
        if value and not _has_ascii_control(value):
            return value
    return fallback


def _coerce_referral_int(value: object, *, fallback: int = 0) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else fallback
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return fallback
        try:
            return int(value)
        except ValueError:
            return fallback
    return fallback


def _referral_to_entry(referral: Referral, email: str | None) -> ReferralEntryResponse:
    return ReferralEntryResponse(
        id=str(getattr(referral, "id", "")),
        email=_coerce_referral_text(email, fallback="Pending invite"),
        status=_coerce_referral_status(referral),
        invited_at=_serialize_referral_timestamp(
            getattr(referral, "created_at", None)
        ) or "",
        converted_at=_serialize_referral_timestamp(
            getattr(referral, "converted_at", None)
        ),
        credits_earned=_coerce_referral_int(
            getattr(referral, "credits_issued_referrer", None)
        ),
    )


def _coerce_referral_status(referral: object) -> str:
    return _coerce_referral_text(getattr(referral, "status", None), fallback="pending")


def _coerce_referral_history_row(row: object) -> tuple[object, object | None] | None:
    try:
        referral = row[0]  # type: ignore[index]
        email = row[1]  # type: ignore[index]
    except (TypeError, IndexError, KeyError):
        return None
    if referral is None or not hasattr(referral, "id"):
        return None
    return referral, email


def _coerce_referral_row_list(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _claim_to_response(referral: Referral) -> ClaimResponse:
    return ClaimResponse(
        status=_coerce_referral_status(referral),
        referral_id=str(getattr(referral, "id", "")),
    )


def _stats_to_response(
    *,
    referral_link: object,
    total_referrals: object,
    pending: object,
    converted: object,
    total_credits_earned: object,
) -> ReferralStatsResponse:
    return ReferralStatsResponse(
        referral_link=_coerce_referral_text(referral_link, fallback=""),
        total_referrals=_coerce_referral_int(total_referrals, fallback=0),
        pending=_coerce_referral_int(pending, fallback=0),
        converted=_coerce_referral_int(converted, fallback=0),
        total_credits_earned=_coerce_referral_int(
            total_credits_earned,
            fallback=0,
        ),
    )


def _build_referral_link(frontend_origin: object, user_id: object) -> str:
    base_url = _coerce_referral_text(frontend_origin, fallback="").rstrip("/")
    return f"{base_url}/auth/signup?ref={quote(str(user_id), safe='')}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ReferralStatsResponse)
async def get_referral_stats(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReferralStatsResponse:
    """Get the current user's referral stats and shareable link."""
    user_id = current_user.id

    # Build referral link using user id
    referral_link = _build_referral_link(get_public_frontend_origin(request), user_id)

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

    return _stats_to_response(
        referral_link=referral_link,
        total_referrals=total_referrals,
        pending=pending,
        converted=converted,
        total_credits_earned=total_credits_earned,
    )


@router.post("/claim", response_model=ClaimResponse)
async def claim_referral_link(
    body: ClaimRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ClaimResponse:
    referral = await claim_referral(
        db,
        referrer_id=body.referrer_id,
        referred_id=current_user.id,
    )
    return _claim_to_response(referral)


@router.post("/convert", response_model=ConvertResponse)
async def convert_referral(
    body: ConvertRequest,
    db: AsyncSession = Depends(get_db),
) -> ConvertResponse:
    """Mark a referral as converted and issue credits to both parties.

    Called internally when a referred user upgrades to a paid plan.
    """
    referrer_id = body.referrer_id
    referred_id = body.referred_id

    referral = await claim_referral(
        db,
        referrer_id=referrer_id,
        referred_id=referred_id,
    )
    if _coerce_referral_status(referral) == "converted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Referral already converted",
        )
    referral = await convert_pending_referral(db, referred_id=referred_id)
    if referral is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Referral not found",
        )

    return ConvertResponse(
        referral_id=str(referral.id),
        referrer_credits_issued=REFERRER_CREDITS,
        referred_credits_issued=REFERRED_CREDITS,
    )


@router.get("/stats", response_model=ReferralStatsResponse)
async def get_referral_stats_alias(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReferralStatsResponse:
    return await get_referral_stats(current_user=current_user, db=db)


@router.get("/history", response_model=list[ReferralEntryResponse])
async def get_referral_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ReferralEntryResponse]:
    result = await db.execute(
        select(Referral, User.email)
        .join(User, User.id == Referral.referred_id, isouter=True)
        .where(Referral.referrer_id == current_user.id)
        .order_by(Referral.created_at.desc())
    )

    entries: list[ReferralEntryResponse] = []
    for row in _coerce_referral_row_list(result.all()):
        parsed_row = _coerce_referral_history_row(row)
        if parsed_row is None:
            continue
        referral, email = parsed_row
        entries.append(_referral_to_entry(referral, email))
    return entries
