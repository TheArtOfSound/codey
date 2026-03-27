from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.billing.plans import PLANS, TOPUP_PACKAGES
from codey.saas.billing.service import BillingError, BillingService
from codey.saas.billing.webhooks import handle_stripe_webhook
from codey.saas.database import get_db
from codey.saas.models import User

router = APIRouter(tags=["billing"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlanFeatures(BaseModel):
    github_repos: int
    autonomous_mode: bool
    priority: bool
    max_upload_mb: int
    seats: int | None = None


class PlanInfo(BaseModel):
    key: str
    name: str
    price_monthly: int
    credits: int
    rollover: int
    features: PlanFeatures


class PlansResponse(BaseModel):
    plans: list[PlanInfo]


class SubscribeRequest(BaseModel):
    plan: str


class SubscribeResponse(BaseModel):
    client_secret: str | None = None
    subscription_id: str | None = None
    type: str


class ConfirmSubscriptionRequest(BaseModel):
    subscription_id: str


class ConfirmSubscriptionResponse(BaseModel):
    plan: str
    credits: int
    subscription_id: str
    status: str


class ChangePlanRequest(BaseModel):
    plan: str


class ChangePlanResponse(BaseModel):
    old_plan: str
    new_plan: str
    credits: int
    subscription_id: str | None


class CancelResponse(BaseModel):
    status: str
    access_until: str
    subscription_id: str | None


class TopupRequest(BaseModel):
    package: str


class TopupResponse(BaseModel):
    client_secret: str


class PaymentMethodResponse(BaseModel):
    id: str
    brand: str
    last4: str
    exp_month: int
    exp_year: int


class AddPaymentMethodResponse(BaseModel):
    client_secret: str


class InvoiceResponse(BaseModel):
    id: str
    number: str | None
    status: str | None
    amount_due: int
    amount_paid: int
    currency: str
    period_start: str
    period_end: str
    hosted_invoice_url: str | None
    pdf: str | None
    created: str


class WebhookResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/billing/plans", response_model=PlansResponse)
async def list_plans() -> PlansResponse:
    plan_list: list[PlanInfo] = []
    for key, plan in PLANS.items():
        features = plan["features"]
        plan_list.append(
            PlanInfo(
                key=key,
                name=plan["name"],
                price_monthly=plan["price_monthly"],
                credits=plan["credits"],
                rollover=plan["rollover"],
                features=PlanFeatures(
                    github_repos=features["github_repos"],
                    autonomous_mode=features["autonomous_mode"],
                    priority=features["priority"],
                    max_upload_mb=features["max_upload_mb"],
                    seats=features.get("seats"),
                ),
            )
        )
    return PlansResponse(plans=plan_list)


@router.post("/billing/subscribe", response_model=SubscribeResponse)
async def subscribe(
    body: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubscribeResponse:
    billing = BillingService(db)
    try:
        result = await billing.create_subscription(current_user.id, body.plan)
    except BillingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return SubscribeResponse(
        client_secret=result.get("client_secret"),
        subscription_id=result.get("subscription_id"),
        type=result["type"],
    )


@router.post("/billing/subscribe/confirm", response_model=ConfirmSubscriptionResponse)
async def confirm_subscription(
    body: ConfirmSubscriptionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConfirmSubscriptionResponse:
    billing = BillingService(db)
    try:
        result = await billing.confirm_subscription(current_user.id, body.subscription_id)
    except BillingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return ConfirmSubscriptionResponse(**result)


@router.post("/billing/change-plan", response_model=ChangePlanResponse)
async def change_plan(
    body: ChangePlanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChangePlanResponse:
    billing = BillingService(db)
    try:
        result = await billing.change_plan(current_user.id, body.plan)
    except BillingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return ChangePlanResponse(**result)


@router.post("/billing/cancel", response_model=CancelResponse)
async def cancel_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CancelResponse:
    billing = BillingService(db)
    try:
        result = await billing.cancel_subscription(current_user.id)
    except BillingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return CancelResponse(**result)


@router.post("/billing/topup", response_model=TopupResponse)
async def topup(
    body: TopupRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TopupResponse:
    billing = BillingService(db)
    try:
        result = await billing.create_topup_payment(current_user.id, body.package)
    except BillingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return TopupResponse(client_secret=result["client_secret"])


@router.get("/billing/payment-methods", response_model=list[PaymentMethodResponse])
async def list_payment_methods(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PaymentMethodResponse]:
    billing = BillingService(db)
    try:
        methods = await billing.get_payment_methods(current_user.id)
    except BillingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return [PaymentMethodResponse(**m) for m in methods]


@router.post("/billing/payment-methods", response_model=AddPaymentMethodResponse)
async def add_payment_method(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AddPaymentMethodResponse:
    billing = BillingService(db)
    try:
        result = await billing.add_payment_method(current_user.id)
    except BillingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return AddPaymentMethodResponse(client_secret=result["client_secret"])


@router.delete("/billing/payment-methods/{pm_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_payment_method(
    pm_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    billing = BillingService(db)
    try:
        await billing.remove_payment_method(current_user.id, pm_id)
    except BillingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get("/billing/invoices", response_model=list[InvoiceResponse])
async def list_invoices(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[InvoiceResponse]:
    billing = BillingService(db)
    try:
        invoices = await billing.get_invoices(current_user.id)
    except BillingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return [InvoiceResponse(**inv) for inv in invoices]


# ---------------------------------------------------------------------------
# Stripe webhook — no auth, uses signature verification
# ---------------------------------------------------------------------------


@router.post("/webhooks/stripe", response_model=WebhookResponse)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> WebhookResponse:
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not sig_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe signature header",
        )

    try:
        result = await handle_stripe_webhook(payload, sig_header, db)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook signature verification failed",
        )

    return WebhookResponse(status=result.get("status", "ok"))
