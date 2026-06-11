from __future__ import annotations

import logging

import pytest

import codey.saas.auth.public_urls as public_urls
import codey.saas.emails.service as email_service


def test_email_service_normalizes_sender_identity(monkeypatch) -> None:
    monkeypatch.setattr(email_service.settings, "sendgrid_api_key", "sg-key")
    monkeypatch.setattr(email_service.settings, "email_from", " noreply@example.com ")
    monkeypatch.setattr(email_service.settings, "email_from_name", " Codey Alerts ")

    class _FakeSendGridClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

    monkeypatch.setattr(email_service.sendgrid, "SendGridAPIClient", _FakeSendGridClient)

    service = email_service.EmailService()

    assert service._from_email is not None
    assert service._from_email.email == "noreply@example.com"
    assert service._from_email.name == "Codey Alerts"


def test_redact_email_log_values() -> None:
    assert email_service._redact_email_address(" user@example.com ") == "***@example.com"
    assert email_service._redact_email_address("not-an-email") == "[redacted]"

    message = email_service._redact_email_error(
        "send failed https://user:url-secret@example.test/api"
        "?token=mail-token&client_secret=query-client-secret "
        "mirror=https://mail.example.test/api#client_secret=fragment-secret "
        "authorization: Bearer SG.secret refresh_token=refresh-secret "
        "password=inline-password for user@example.com"
    )

    assert "url-secret" not in message
    assert "mail-token" not in message
    assert "query-client-secret" not in message
    assert "fragment-secret" not in message
    assert "SG.secret" not in message
    assert "refresh-secret" not in message
    assert "inline-password" not in message
    assert "user@example.com" not in message
    assert "https://***@example.test/api?token=***&client_secret=***" in message
    assert "refresh_token=***" in message
    assert "password=***" in message
    assert "authorization: Bearer ***" in message
    assert "***@example.com" in message


@pytest.mark.asyncio
async def test_send_email_skips_client_when_sendgrid_key_is_whitespace(monkeypatch) -> None:
    monkeypatch.setattr(email_service.settings, "sendgrid_api_key", "   ")

    def failing_client(*args, **kwargs):
        raise AssertionError("SendGrid client should not be constructed for blank keys")

    monkeypatch.setattr(email_service.sendgrid, "SendGridAPIClient", failing_client)

    service = email_service.EmailService()

    assert service._client is None
    assert await service.send_email("user@example.com", "Hello", "<p>hi</p>") is False


@pytest.mark.asyncio
async def test_send_email_redacts_provider_failures_in_logs(monkeypatch, caplog) -> None:
    monkeypatch.setattr(email_service.settings, "sendgrid_api_key", "sg-key")
    monkeypatch.setattr(email_service.settings, "email_from", "noreply@example.com")
    monkeypatch.setattr(email_service.settings, "email_from_name", "Codey")

    class _FailingSendGridClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def send(self, _mail) -> None:
            raise RuntimeError(
                "send failed https://user:url-secret@example.test/api"
                "?token=mail-token&client_secret=query-client-secret"
                " authorization: Bearer SG.secret refresh_token=refresh-secret"
                " password=inline-password for user@example.com"
            )

    monkeypatch.setattr(
        email_service.sendgrid,
        "SendGridAPIClient",
        _FailingSendGridClient,
    )
    caplog.set_level(logging.WARNING, logger="codey.saas.emails.service")

    service = email_service.EmailService()

    assert await service.send_email("person@example.com", "Hello", "<p>hi</p>") is False
    assert "person@example.com" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "url-secret" not in caplog.text
    assert "mail-token" not in caplog.text
    assert "query-client-secret" not in caplog.text
    assert "SG.secret" not in caplog.text
    assert "refresh-secret" not in caplog.text
    assert "inline-password" not in caplog.text
    assert "user@example.com" not in caplog.text
    assert "https://***@example.test/api?token=***&client_secret=***" in caplog.text
    assert "refresh_token=***" in caplog.text
    assert "password=***" in caplog.text
    assert "authorization: Bearer ***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_send_email_skips_client_when_sendgrid_key_has_control_character(
    monkeypatch,
) -> None:
    monkeypatch.setattr(email_service.settings, "sendgrid_api_key", "sg\tkey")

    def failing_client(*args, **kwargs):
        raise AssertionError("SendGrid client should not be constructed for bad keys")

    monkeypatch.setattr(email_service.sendgrid, "SendGridAPIClient", failing_client)

    service = email_service.EmailService()

    assert service._client is None
    assert await service.send_email("user@example.com", "Hello", "<p>hi</p>") is False


@pytest.mark.asyncio
async def test_send_email_skips_client_when_sendgrid_key_has_internal_whitespace(
    monkeypatch,
) -> None:
    monkeypatch.setattr(email_service.settings, "sendgrid_api_key", "sg key")
    monkeypatch.setattr(email_service.settings, "email_from", "noreply@example.com")
    monkeypatch.setattr(email_service.settings, "email_from_name", "Codey Alerts")

    def failing_client(*args, **kwargs):
        raise AssertionError("SendGrid client should not be constructed for bad keys")

    monkeypatch.setattr(email_service.sendgrid, "SendGridAPIClient", failing_client)

    service = email_service.EmailService()

    assert service._client is None
    assert service._from_email is not None
    assert service._from_email.name == "Codey Alerts"
    assert await service.send_email("user@example.com", "Hello", "<p>hi</p>") is False


@pytest.mark.asyncio
async def test_send_email_skips_when_sender_address_is_whitespace(monkeypatch) -> None:
    monkeypatch.setattr(email_service.settings, "sendgrid_api_key", "sg-key")
    monkeypatch.setattr(email_service.settings, "email_from", "   ")
    monkeypatch.setattr(email_service.settings, "email_from_name", " Codey ")

    class _FakeSendGridClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.send_called = False

        def send(self, mail) -> None:
            self.send_called = True
            raise AssertionError("Email send should not be attempted without a sender")

    client = _FakeSendGridClient("sg-key")
    monkeypatch.setattr(email_service.sendgrid, "SendGridAPIClient", lambda api_key: client)

    service = email_service.EmailService()

    assert service._from_email is None
    assert await service.send_email("user@example.com", "Hello", "<p>hi</p>") is False
    assert client.send_called is False


@pytest.mark.asyncio
async def test_send_email_skips_when_sender_address_has_control_character(
    monkeypatch,
) -> None:
    monkeypatch.setattr(email_service.settings, "sendgrid_api_key", "sg-key")
    monkeypatch.setattr(email_service.settings, "email_from", "noreply\n@example.com")
    monkeypatch.setattr(email_service.settings, "email_from_name", " Codey ")

    class _FakeSendGridClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.send_called = False

        def send(self, mail) -> None:
            self.send_called = True
            raise AssertionError("Email send should not be attempted without a sender")

    client = _FakeSendGridClient("sg-key")
    monkeypatch.setattr(email_service.sendgrid, "SendGridAPIClient", lambda api_key: client)

    service = email_service.EmailService()

    assert service._from_email is None
    assert await service.send_email("user@example.com", "Hello", "<p>hi</p>") is False
    assert client.send_called is False


@pytest.mark.asyncio
async def test_send_email_skips_when_sender_address_has_internal_whitespace(
    monkeypatch,
) -> None:
    monkeypatch.setattr(email_service.settings, "sendgrid_api_key", "sg-key")
    monkeypatch.setattr(email_service.settings, "email_from", "no reply@example.com")
    monkeypatch.setattr(email_service.settings, "email_from_name", " Codey Alerts ")

    class _FakeSendGridClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.send_called = False

        def send(self, mail) -> None:
            self.send_called = True
            raise AssertionError("Email send should not be attempted without a sender")

    client = _FakeSendGridClient("sg-key")
    monkeypatch.setattr(email_service.sendgrid, "SendGridAPIClient", lambda api_key: client)

    service = email_service.EmailService()

    assert service._from_email is None
    assert await service.send_email("user@example.com", "Hello", "<p>hi</p>") is False
    assert client.send_called is False


@pytest.mark.asyncio
async def test_send_password_reset_uses_normalized_frontend_origin(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_password_reset(*, reset_url: str):
        captured["reset_url"] = reset_url
        return "Reset", "<p>reset</p>"

    async def fake_send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        captured["to_email"] = to_email
        captured["subject"] = subject
        captured["html_content"] = html_content
        return True

    monkeypatch.setattr(email_service.templates, "password_reset", fake_password_reset)
    monkeypatch.setattr(email_service.EmailService, "send_email", fake_send_email)
    monkeypatch.setattr(public_urls.settings, "frontend_url", "   ")

    service = email_service.EmailService()
    result = await service.send_password_reset("user@example.com", "token-123")

    assert result is True
    assert captured["reset_url"] == "/auth/reset-password?token=token-123"
    assert captured["to_email"] == "user@example.com"
    assert captured["subject"] == "Reset"


@pytest.mark.asyncio
async def test_send_password_reset_url_encodes_token(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_password_reset(*, reset_url: str):
        captured["reset_url"] = reset_url
        return "Reset", "<p>reset</p>"

    async def fake_send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        return True

    monkeypatch.setattr(email_service.templates, "password_reset", fake_password_reset)
    monkeypatch.setattr(email_service.EmailService, "send_email", fake_send_email)

    service = email_service.EmailService()
    result = await service.send_password_reset(
        "user@example.com",
        "token value&next=/evil",
        frontend_origin="https://app.example.com",
    )

    assert result is True
    assert (
        captured["reset_url"]
        == "https://app.example.com/auth/reset-password?token=token%20value%26next%3D%2Fevil"
    )


@pytest.mark.asyncio
async def test_send_verification_url_encodes_token(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_email_verification(*, verification_url: str):
        captured["verification_url"] = verification_url
        return "Verify", "<p>verify</p>"

    async def fake_send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        return True

    monkeypatch.setattr(email_service.templates, "email_verification", fake_email_verification)
    monkeypatch.setattr(email_service.EmailService, "send_email", fake_send_email)
    monkeypatch.setattr(public_urls.settings, "frontend_url", "https://app.example.com")

    service = email_service.EmailService()
    result = await service.send_verification(
        "user@example.com",
        "verify token&next=/evil",
    )

    assert result is True
    assert (
        captured["verification_url"]
        == "https://app.example.com/verify-email?token=verify%20token%26next%3D%2Fevil"
    )


@pytest.mark.asyncio
async def test_send_welcome_normalizes_explicit_frontend_origin(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_welcome(*, name: str, dashboard_url: str):
        captured["name"] = name
        captured["dashboard_url"] = dashboard_url
        return "Welcome", "<p>welcome</p>"

    async def fake_send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        captured["to_email"] = to_email
        captured["subject"] = subject
        captured["html_content"] = html_content
        return True

    monkeypatch.setattr(email_service.templates, "welcome", fake_welcome)
    monkeypatch.setattr(email_service.EmailService, "send_email", fake_send_email)
    monkeypatch.setattr(public_urls.settings, "frontend_url", "   ")

    service = email_service.EmailService()
    result = await service.send_welcome(
        "user@example.com",
        "Repo User",
        frontend_origin=" https://app.example.com/ ",
    )

    assert result is True
    assert captured["name"] == "Repo User"
    assert captured["dashboard_url"] == "https://app.example.com/dashboard"
    assert captured["to_email"] == "user@example.com"
    assert captured["subject"] == "Welcome"


@pytest.mark.asyncio
async def test_send_subscription_cancelled_uses_billing_settings_page(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_subscription_cancelled(*, end_date: str, resubscribe_url: str):
        captured["end_date"] = end_date
        captured["resubscribe_url"] = resubscribe_url
        return "Cancelled", "<p>cancelled</p>"

    async def fake_send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        captured["to_email"] = to_email
        captured["subject"] = subject
        captured["html_content"] = html_content
        return True

    monkeypatch.setattr(
        email_service.templates,
        "subscription_cancelled",
        fake_subscription_cancelled,
    )
    monkeypatch.setattr(email_service.EmailService, "send_email", fake_send_email)
    monkeypatch.setattr(public_urls.settings, "frontend_url", " https://app.example.com/ ")

    service = email_service.EmailService()
    result = await service.send_subscription_cancelled(
        "user@example.com",
        "2026-05-31",
    )

    assert result is True
    assert captured["end_date"] == "2026-05-31"
    assert captured["resubscribe_url"] == "https://app.example.com/settings/billing"
    assert captured["to_email"] == "user@example.com"
    assert captured["subject"] == "Cancelled"
