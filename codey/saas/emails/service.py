from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import quote

try:
    import sendgrid
    from sendgrid.helpers.mail import Content, Email, Mail, To
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-light tests
    if exc.name != "sendgrid":
        raise
    _SENDGRID_IMPORT_ERROR: ModuleNotFoundError | None = exc

    def _raise_missing_sendgrid(*args, **kwargs):
        raise RuntimeError("sendgrid is required for email delivery") from _SENDGRID_IMPORT_ERROR

    class _MissingSendGrid:
        SendGridAPIClient = staticmethod(_raise_missing_sendgrid)

    class Email:  # type: ignore[no-redef]
        def __init__(self, email: str, name: str | None = None) -> None:
            self.email = email
            self.name = name

    class To:  # type: ignore[no-redef]
        def __init__(self, email: str) -> None:
            self.email = email

    class Content:  # type: ignore[no-redef]
        def __init__(self, mime_type: str, content: str) -> None:
            self.mime_type = mime_type
            self.content = content

    class Mail:  # type: ignore[no-redef]
        def __init__(
            self,
            *,
            from_email: Email,
            to_emails: To,
            subject: str,
            html_content: Content,
        ) -> None:
            self.from_email = from_email
            self.to_emails = to_emails
            self.subject = subject
            self.html_content = html_content

    sendgrid: Any = _MissingSendGrid()
else:  # pragma: no cover - depends on optional runtime dependency
    _SENDGRID_IMPORT_ERROR = None

from codey.saas.auth.public_urls import (
    _normalize_frontend_origin,
    get_public_frontend_origin,
)
from codey.saas.config import settings
from codey.saas.emails import templates

logger = logging.getLogger(__name__)
_URL_CREDENTIAL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&#](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|token|secret)=)[^&#\s]+"
)
_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|token|secret|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_non_empty_email_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if _has_ascii_control(normalized):
        return None
    return normalized or None


def _coerce_non_empty_email_secret(value: object) -> str | None:
    normalized = _coerce_non_empty_email_text(value)
    if normalized is None or _has_whitespace(normalized):
        return None
    return normalized


def _coerce_non_empty_email_address(value: object) -> str | None:
    normalized = _coerce_non_empty_email_text(value)
    if normalized is None or _has_whitespace(normalized):
        return None
    return normalized


def _normalized_sender_email() -> str | None:
    return _coerce_non_empty_email_address(settings.email_from)


def _normalized_sender_name() -> str | None:
    return _coerce_non_empty_email_text(settings.email_from_name)


def _frontend_base_url() -> str:
    return get_public_frontend_origin()


def _resolved_frontend_base_url(frontend_origin: str | None = None) -> str:
    return _normalize_frontend_origin(frontend_origin) or _frontend_base_url()


def _email_url_token(value: object) -> str:
    return quote(str(value), safe="")


def _redact_email_address(value: object) -> str:
    email = str(value).strip()
    if "@" not in email:
        return "[redacted]"
    _local, domain = email.rsplit("@", 1)
    domain = domain.strip() or "unknown"
    return f"***@{domain}"


def _redact_email_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _NAMED_SECRET_RE.sub(r"\1***", text)
    return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)


class EmailService:
    """SendGrid-backed transactional email service for Codey."""

    def __init__(self) -> None:
        self._resend_key = _coerce_non_empty_email_secret(settings.resend_api_key)
        api_key = _coerce_non_empty_email_secret(settings.sendgrid_api_key)
        self._client = (
            sendgrid.SendGridAPIClient(api_key=api_key) if api_key else None
        )
        sender_email = _normalized_sender_email()
        sender_name = _normalized_sender_name()
        self._from_email = (
            Email(sender_email, sender_name) if sender_email is not None else None
        )

    # ------------------------------------------------------------------
    # Core sender
    # ------------------------------------------------------------------

    async def send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        """Send a single transactional email. Returns True on success."""
        safe_to_email = _redact_email_address(to_email)
        if self._resend_key is not None:
            if self._from_email is None:
                logger.info("Email sender not configured; skipping email to %s", safe_to_email)
                return False
            _from_addr = getattr(self._from_email, "email", None) or ""
            _from_name = getattr(self._from_email, "name", None)
            _sender = f"{_from_name} <{_from_addr}>" if _from_name else _from_addr
            try:
                import httpx
                async with httpx.AsyncClient(timeout=15.0) as _hc:
                    _resp = await _hc.post(
                        "https://api.resend.com/emails",
                        headers={"Authorization": f"Bearer {self._resend_key}", "Content-Type": "application/json"},
                        json={"from": _sender, "to": [to_email], "subject": subject, "html": html_content},
                    )
                if _resp.status_code >= 400:
                    logger.error("Resend returned %s for %s: %s", _resp.status_code, safe_to_email, _redact_email_error(_resp.text))
                    return False
                logger.info("Email sent (Resend) to %s subject: %s", safe_to_email, subject)
                return True
            except Exception as exc:
                logger.warning("Failed to send email (Resend) to %s: %s", safe_to_email, _redact_email_error(exc))
                return False
        if self._client is None:
            logger.info("SendGrid not configured; skipping email to %s", safe_to_email)
            return False
        if self._from_email is None:
            logger.info("Email sender not configured; skipping email to %s", safe_to_email)
            return False

        mail = Mail(
            from_email=self._from_email,
            to_emails=To(to_email),
            subject=subject,
            html_content=Content("text/html", html_content),
        )
        try:
            response = await asyncio.to_thread(self._client.send, mail)
            if response.status_code >= 400:
                logger.error(
                    "SendGrid returned %s for %s: %s",
                    response.status_code,
                    safe_to_email,
                    _redact_email_error(response.body),
                )
                return False
            logger.info("Email sent to %s — subject: %s", safe_to_email, subject)
            return True
        except Exception as exc:
            logger.warning(
                "Failed to send email to %s: %s",
                safe_to_email,
                _redact_email_error(exc),
            )
            return False

    # ------------------------------------------------------------------
    # Template helpers
    # ------------------------------------------------------------------

    async def send_welcome(
        self,
        email: str,
        name: str,
        *,
        frontend_origin: str | None = None,
    ) -> bool:
        base_url = _resolved_frontend_base_url(frontend_origin)
        subject, html = templates.welcome(
            name=name,
            dashboard_url=f"{base_url}/dashboard",
        )
        return await self.send_email(email, subject, html)

    async def send_verification(self, email: str, token: str) -> bool:
        subject, html = templates.email_verification(
            verification_url=f"{_frontend_base_url()}/verify-email?token={_email_url_token(token)}",
        )
        return await self.send_email(email, subject, html)

    async def send_payment_success(
        self,
        email: str,
        amount_cents: int,
        credits_added: int,
        new_balance: int,
    ) -> bool:
        subject, html = templates.payment_success(
            amount_cents=amount_cents,
            credits_added=credits_added,
            new_balance=new_balance,
        )
        return await self.send_email(email, subject, html)

    async def send_payment_failed(self, email: str) -> bool:
        subject, html = templates.payment_failed(
            dashboard_url=_frontend_base_url(),
        )
        return await self.send_email(email, subject, html)

    async def send_low_credits(self, email: str, remaining: int, monthly: int) -> bool:
        subject, html = templates.low_credits(
            remaining=remaining,
            monthly=monthly,
            topup_url=f"{_frontend_base_url()}/dashboard/credits",
        )
        return await self.send_email(email, subject, html)

    async def send_credits_exhausted(self, email: str) -> bool:
        subject, html = templates.credits_exhausted(
            topup_url=f"{_frontend_base_url()}/dashboard/credits",
        )
        return await self.send_email(email, subject, html)

    async def send_autonomous_summary(
        self, email: str, actions: list[dict], credits_used: int
    ) -> bool:
        subject, html = templates.autonomous_summary(
            actions=actions,
            credits_used=credits_used,
            dashboard_url=f"{_frontend_base_url()}/dashboard",
        )
        return await self.send_email(email, subject, html)

    async def send_session_complete(self, email: str, session_summary: dict) -> bool:
        subject, html = templates.session_complete(
            session_summary=session_summary,
            dashboard_url=f"{_frontend_base_url()}/dashboard",
        )
        return await self.send_email(email, subject, html)

    async def send_subscription_cancelled(self, email: str, end_date: str) -> bool:
        subject, html = templates.subscription_cancelled(
            end_date=end_date,
            resubscribe_url=f"{_frontend_base_url()}/settings/billing",
        )
        return await self.send_email(email, subject, html)

    async def send_password_reset(
        self,
        email: str,
        token: str,
        *,
        frontend_origin: str | None = None,
    ) -> bool:
        base_url = _resolved_frontend_base_url(frontend_origin)
        subject, html = templates.password_reset(
            reset_url=f"{base_url}/auth/reset-password?token={_email_url_token(token)}",
        )
        return await self.send_email(email, subject, html)
