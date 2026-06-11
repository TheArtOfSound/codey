from __future__ import annotations

import math
import re
from urllib.parse import urlparse

import stripe
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

_BILLING_URL_CREDENTIALS_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_BILLING_QUERY_SECRET_RE = re.compile(
    r"([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token)=)[^&#\s]+",
    re.IGNORECASE,
)
_BILLING_NAMED_SECRET_RE = re.compile(
    r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|secret|token|authorization)\b(\s*[:=]\s*)"
    r"(?:Bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
_BILLING_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)


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
    is_default: bool = False


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
# Helpers
# ---------------------------------------------------------------------------


def _coerce_billing_text(value: object) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return None


def _has_billing_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _has_billing_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _coerce_billing_secret(value: object) -> str | None:
    normalized = _coerce_billing_text(value)
    if normalized is None or _has_billing_whitespace(normalized):
        return None
    return normalized


def _coerce_billing_public_url(value: object) -> str | None:
    url = _coerce_billing_text(value)
    if url is None or _has_billing_ascii_control(url):
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.netloc or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    if port is not None and not (1 <= port <= 65535):
        return None
    if _BILLING_QUERY_SECRET_RE.search(f"?{parsed.query}"):
        return None
    if _BILLING_QUERY_SECRET_RE.search(f"?{parsed.fragment}"):
        return None
    return url


def _redact_billing_error(value: object) -> str:
    text = _BILLING_URL_CREDENTIALS_RE.sub(r"\1***@", str(value))
    text = _BILLING_QUERY_SECRET_RE.sub(r"\1***", text)

    def _replace_named_secret(match: re.Match[str]) -> str:
        prefix = f"{match.group(1)}{match.group(2)}"
        if "bearer" in match.group(0).lower():
            return f"{prefix}Bearer ***"
        return f"{prefix}***"

    text = _BILLING_NAMED_SECRET_RE.sub(_replace_named_secret, text)
    return _BILLING_EMAIL_RE.sub("[redacted-email]", text)


def _coerce_billing_int(value: object, fallback: int = 0) -> int:
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


def _coerce_billing_optional_int(value: object) -> int | None:
    normalized: float
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        normalized = value
    elif isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            normalized = float(value)
        except ValueError:
            return None
    else:
        return None
    return int(normalized) if math.isfinite(normalized) else None


def _coerce_billing_bool(value: object, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        normalized = float(value)
        return bool(normalized) if math.isfinite(normalized) else fallback
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"true", "1", "yes", "y", "on"}:
            return True
        if value in {"false", "0", "no", "n", "off", ""}:
            return False
    return fallback


def _payment_method_to_response(method: object) -> PaymentMethodResponse:
    payload = method if isinstance(method, dict) else {}
    return PaymentMethodResponse(
        id=_coerce_billing_text(payload.get("id")) or "",
        brand=_coerce_billing_text(payload.get("brand")) or "unknown",
        last4=_coerce_billing_text(payload.get("last4")) or "",
        exp_month=_coerce_billing_int(payload.get("exp_month"), 0),
        exp_year=_coerce_billing_int(payload.get("exp_year"), 0),
        is_default=_coerce_billing_bool(payload.get("is_default"), False),
    )


def _invoice_to_response(invoice: object) -> InvoiceResponse:
    payload = invoice if isinstance(invoice, dict) else {}
    return InvoiceResponse(
        id=_coerce_billing_text(payload.get("id")) or "",
        number=_coerce_billing_text(payload.get("number")),
        status=_coerce_billing_text(payload.get("status")),
        amount_due=_coerce_billing_int(payload.get("amount_due"), 0),
        amount_paid=_coerce_billing_int(payload.get("amount_paid"), 0),
        currency=_coerce_billing_text(payload.get("currency")) or "",
        period_start=_coerce_billing_text(payload.get("period_start")) or "",
        period_end=_coerce_billing_text(payload.get("period_end")) or "",
        hosted_invoice_url=_coerce_billing_public_url(payload.get("hosted_invoice_url")),
        pdf=_coerce_billing_public_url(payload.get("pdf")),
        created=_coerce_billing_text(payload.get("created")) or "",
    )


def _subscribe_to_response(result: object) -> SubscribeResponse:
    payload = result if isinstance(result, dict) else {}
    return SubscribeResponse(
        client_secret=_coerce_billing_secret(payload.get("client_secret")),
        subscription_id=_coerce_billing_text(payload.get("subscription_id")),
        type=_coerce_billing_text(payload.get("type")) or "",
    )


def _confirm_subscription_to_response(result: object) -> ConfirmSubscriptionResponse:
    payload = result if isinstance(result, dict) else {}
    return ConfirmSubscriptionResponse(
        plan=_coerce_billing_text(payload.get("plan")) or "",
        credits=_coerce_billing_int(payload.get("credits"), 0),
        subscription_id=_coerce_billing_text(payload.get("subscription_id")) or "",
        status=_coerce_billing_text(payload.get("status")) or "",
    )


def _change_plan_to_response(result: object) -> ChangePlanResponse:
    payload = result if isinstance(result, dict) else {}
    return ChangePlanResponse(
        old_plan=_coerce_billing_text(payload.get("old_plan")) or "",
        new_plan=_coerce_billing_text(payload.get("new_plan")) or "",
        credits=_coerce_billing_int(payload.get("credits"), 0),
        subscription_id=_coerce_billing_text(payload.get("subscription_id")),
    )


def _cancel_to_response(result: object) -> CancelResponse:
    payload = result if isinstance(result, dict) else {}
    return CancelResponse(
        status=_coerce_billing_text(payload.get("status")) or "",
        access_until=_coerce_billing_text(payload.get("access_until")) or "",
        subscription_id=_coerce_billing_text(payload.get("subscription_id")),
    )


def _topup_to_response(result: object) -> TopupResponse:
    payload = result if isinstance(result, dict) else {}
    return TopupResponse(
        client_secret=_coerce_billing_secret(payload.get("client_secret")) or "",
    )


def _add_payment_method_to_response(result: object) -> AddPaymentMethodResponse:
    payload = result if isinstance(result, dict) else {}
    return AddPaymentMethodResponse(
        client_secret=_coerce_billing_secret(payload.get("client_secret")) or "",
    )


def _webhook_to_response(result: object) -> WebhookResponse:
    payload = result if isinstance(result, dict) else {}
    return WebhookResponse(
        status=_coerce_billing_text(payload.get("status")) or "ok",
    )


def _plan_to_response(key: object, plan: object) -> PlanInfo:
    key_text = _coerce_billing_text(key) or ""
    payload = plan if isinstance(plan, dict) else {}
    features = payload.get("features") if isinstance(payload.get("features"), dict) else {}
    return PlanInfo(
        key=key_text,
        name=_coerce_billing_text(payload.get("name")) or key_text or "Plan",
        price_monthly=_coerce_billing_int(payload.get("price_monthly"), 0),
        credits=_coerce_billing_int(payload.get("credits"), 0),
        rollover=_coerce_billing_int(payload.get("rollover"), 0),
        features=PlanFeatures(
            github_repos=_coerce_billing_int(features.get("github_repos"), 0),
            autonomous_mode=_coerce_billing_bool(features.get("autonomous_mode"), False),
            priority=_coerce_billing_bool(features.get("priority"), False),
            max_upload_mb=_coerce_billing_int(features.get("max_upload_mb"), 0),
            seats=_coerce_billing_optional_int(features.get("seats")),
        ),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/billing/plans", response_model=PlansResponse)
async def list_plans() -> PlansResponse:
    return PlansResponse(
        plans=[_plan_to_response(key, plan) for key, plan in PLANS.items()]
    )


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
            detail=_redact_billing_error(exc),
        )
    return _subscribe_to_response(result)


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
            detail=_redact_billing_error(exc),
        )
    return _confirm_subscription_to_response(result)


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
            detail=_redact_billing_error(exc),
        )
    return _change_plan_to_response(result)


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
            detail=_redact_billing_error(exc),
        )
    return _cancel_to_response(result)


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
            detail=_redact_billing_error(exc),
        )
    return _topup_to_response(result)


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
            detail=_redact_billing_error(exc),
        )
    return [_payment_method_to_response(m) for m in methods]


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
            detail=_redact_billing_error(exc),
        )
    return _add_payment_method_to_response(result)


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
            detail=_redact_billing_error(exc),
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
            detail=_redact_billing_error(exc),
        )
    return [_invoice_to_response(inv) for inv in invoices]


# ---------------------------------------------------------------------------
# Stripe webhook — no auth, uses signature verification
# ---------------------------------------------------------------------------


@router.post("/webhooks/stripe", response_model=WebhookResponse)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> WebhookResponse:
    payload = await request.body()
    sig_header = _coerce_billing_text(request.headers.get("stripe-signature")) or ""

    if not sig_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe signature header",
        )

    try:
        result = await handle_stripe_webhook(payload, sig_header, db)
    except (stripe.error.SignatureVerificationError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook signature verification failed",
        )

    return _webhook_to_response(result)
