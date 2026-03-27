from __future__ import annotations

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
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/balance", response_model=CreditBalanceResponse)
async def get_balance(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreditBalanceResponse:
    credit_service = CreditService(db)
    balance = await credit_service.get_balance(current_user.id)
    return CreditBalanceResponse(**balance)


@router.get("/history", response_model=TransactionHistoryResponse)
async def get_history(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TransactionHistoryResponse:
    credit_service = CreditService(db)
    transactions = await credit_service.get_transaction_history(
        current_user.id, limit=limit, offset=offset
    )
    return TransactionHistoryResponse(
        transactions=[TransactionEntry(**tx) for tx in transactions],
        limit=limit,
        offset=offset,
    )


@router.get("/estimate", response_model=CreditEstimateResponse)
async def estimate_cost(
    prompt: str = Query(..., min_length=1),
    mode: str = Query(default="prompt"),
) -> CreditEstimateResponse:
    estimated = CreditService.estimate_cost(prompt, mode)
    return CreditEstimateResponse(
        estimated_credits=estimated,
        prompt_length=len(prompt),
        mode=mode,
    )
