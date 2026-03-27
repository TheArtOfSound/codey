from __future__ import annotations

import asyncio
import logging

import sendgrid
from sendgrid.helpers.mail import Content, Email, Mail, To

from codey.saas.config import settings
from codey.saas.emails import templates

logger = logging.getLogger(__name__)


class EmailService:
    """SendGrid-backed transactional email service for Codey."""

    def __init__(self) -> None:
        self._client = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
        self._from_email = Email(settings.email_from, settings.email_from_name)

    # ------------------------------------------------------------------
    # Core sender
    # ------------------------------------------------------------------

    async def send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        """Send a single transactional email. Returns True on success."""
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
                    to_email,
                    response.body,
                )
                return False
            logger.info("Email sent to %s — subject: %s", to_email, subject)
            return True
        except Exception:
            logger.exception("Failed to send email to %s", to_email)
            return False

    # ------------------------------------------------------------------
    # Template helpers
    # ------------------------------------------------------------------

    async def send_welcome(self, email: str, name: str) -> bool:
        subject, html = templates.welcome(
            name=name,
            dashboard_url=f"{settings.frontend_url}/dashboard",
        )
        return await self.send_email(email, subject, html)

    async def send_verification(self, email: str, token: str) -> bool:
        subject, html = templates.email_verification(
            verification_url=f"{settings.frontend_url}/verify-email?token={token}",
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
            dashboard_url=settings.frontend_url,
        )
        return await self.send_email(email, subject, html)

    async def send_low_credits(self, email: str, remaining: int, monthly: int) -> bool:
        subject, html = templates.low_credits(
            remaining=remaining,
            monthly=monthly,
            topup_url=f"{settings.frontend_url}/dashboard/credits",
        )
        return await self.send_email(email, subject, html)

    async def send_credits_exhausted(self, email: str) -> bool:
        subject, html = templates.credits_exhausted(
            topup_url=f"{settings.frontend_url}/dashboard/credits",
        )
        return await self.send_email(email, subject, html)

    async def send_autonomous_summary(
        self, email: str, actions: list[dict], credits_used: int
    ) -> bool:
        subject, html = templates.autonomous_summary(
            actions=actions,
            credits_used=credits_used,
            dashboard_url=f"{settings.frontend_url}/dashboard",
        )
        return await self.send_email(email, subject, html)

    async def send_session_complete(self, email: str, session_summary: dict) -> bool:
        subject, html = templates.session_complete(
            session_summary=session_summary,
            dashboard_url=f"{settings.frontend_url}/dashboard",
        )
        return await self.send_email(email, subject, html)

    async def send_subscription_cancelled(self, email: str, end_date: str) -> bool:
        subject, html = templates.subscription_cancelled(
            end_date=end_date,
            resubscribe_url=f"{settings.frontend_url}/dashboard/settings/billing",
        )
        return await self.send_email(email, subject, html)

    async def send_password_reset(self, email: str, token: str) -> bool:
        subject, html = templates.password_reset(
            reset_url=f"{settings.frontend_url}/reset-password?token={token}",
        )
        return await self.send_email(email, subject, html)
