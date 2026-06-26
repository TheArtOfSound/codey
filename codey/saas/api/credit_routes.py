from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.credits.service import CreditService, PLAN_CREDITS
from codey.saas.database import get_db
from codey.saas.models import User

router = APIRouter(prefix="/credits", tags=["credits"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreditBalanceResponse(BaseModel):
    subscription_credits: int
    topup_credits: int
    total: int
    used_this_month: int
    plan: str
    monthly_allocation: int


class TransactionEntry(BaseModel):
    id: str
    amount: int
    type: str
    description: str | None
    credits_before: int | None
    credits_after: int | None
    session_id: str | None
    created_at: str


class TransactionHistoryResponse(BaseModel):
    transactions: list[TransactionEntry]
    limit: int
    offset: int


class CreditEstimateResponse(BaseModel):
    estimated_credits: int
    prompt_length: int
    mode: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_credit_int(value: object, fallback: int = 0) -> int:
    normalized: float
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        normalized = value
    elif isinstance(value, str):
        value = value.strip()
        if not value:
            return fallback
        try:
            normalized = float(value)
        except ValueError:
            return fallback
    else:
        return fallback
    return int(normalized) if math.isfinite(normalized) else fallback


def _coerce_credit_text(value: object) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return None


def _coerce_optional_credit_int(value: object) -> int | None:
    if value is None:
        return None
    return _coerce_credit_int(value, 0)


def _balance_to_response(balance: object) -> CreditBalanceResponse:
    payload = balance if isinstance(balance, dict) else {}
    return CreditBalanceResponse(
        subscription_credits=_coerce_credit_int(payload.get("subscription_credits"), 0),
        topup_credits=_coerce_credit_int(payload.get("topup_credits"), 0),
        total=_coerce_credit_int(payload.get("total"), 0),
        used_this_month=_coerce_credit_int(payload.get("used_this_month"), 0),
        plan=_coerce_credit_text(payload.get("plan")) or "",
        monthly_allocation=_coerce_credit_int(payload.get("monthly_allocation"), 0),
    )


def _transaction_to_response(tx: object) -> TransactionEntry:
    payload = tx if isinstance(tx, dict) else {}
    return TransactionEntry(
        id=_coerce_credit_text(payload.get("id")) or "",
        amount=_coerce_credit_int(payload.get("amount"), 0),
        type=_coerce_credit_text(payload.get("type")) or "",
        description=_coerce_credit_text(payload.get("description")),
        credits_before=_coerce_optional_credit_int(payload.get("credits_before")),
        credits_after=_coerce_optional_credit_int(payload.get("credits_after")),
        session_id=_coerce_credit_text(payload.get("session_id")),
        created_at=_coerce_credit_text(payload.get("created_at")) or "",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/balance", response_model=CreditBalanceResponse)
async def get_balance(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreditBalanceResponse:
    credit_service = CreditService(db)
    balance = await credit_service.get_balance(current_user.id)
    return _balance_to_response(balance)


@router.get("/history", response_model=TransactionHistoryResponse)
async def get_history(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TransactionHistoryResponse:
    if not isinstance(limit, int):
        default_limit = getattr(limit, "default", 50)
        limit = default_limit if isinstance(default_limit, int) else 50
    if not isinstance(offset, int):
        default_offset = getattr(offset, "default", 0)
        offset = default_offset if isinstance(default_offset, int) else 0

    credit_service = CreditService(db)
    transactions = await credit_service.get_transaction_history(
        current_user.id, limit=limit, offset=offset
    )
    return TransactionHistoryResponse(
        transactions=[_transaction_to_response(tx) for tx in transactions],
        limit=limit,
        offset=offset,
    )


@router.get("/estimate", response_model=CreditEstimateResponse)
async def estimate_cost(
    prompt: str = Query(..., min_length=1),
    mode: str = Query(default="prompt"),
) -> CreditEstimateResponse:
    if not isinstance(mode, str):
        default_mode = getattr(mode, "default", "prompt")
        mode = default_mode if isinstance(default_mode, str) else "prompt"

    estimated = CreditService.estimate_cost(prompt, mode)
    return CreditEstimateResponse(
        estimated_credits=estimated,
        prompt_length=len(prompt),
        mode=mode,
    )
