from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace
import uuid

import pytest

import codey.saas.auth.service as auth_service
import codey.saas.emails.service as email_service
import codey.saas.emails.templates as email_templates
from codey.saas.config import settings


class _FakeDB:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


class _ScalarResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


def test_module_initialization_rejects_whitespace_stripe_secret_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stripe_secret_key", "   ")

    reloaded = importlib.reload(auth_service)

    assert reloaded.stripe.api_key == ""


def test_auth_text_update_only_fills_missing_text() -> None:
    assert auth_service._auth_text_update(None, " Test User ") == "Test User"
    assert auth_service._auth_text_update("   ", " Test User ") == "Test User"
    assert auth_service._auth_text_update("Existing", " Test User ") is None
    assert auth_service._auth_text_update(None, ["Test User"]) is None


def test_auth_subject_accepts_uuid_and_rejects_malformed_values() -> None:
    user_id = uuid.uuid4()

    assert auth_service._coerce_auth_subject(user_id) == str(user_id)
    assert auth_service._coerce_auth_subject(" user-1 ") == "user-1"
    assert auth_service._coerce_auth_subject("__invalid__") is None
    assert auth_service._coerce_auth_subject(["user-1"]) is None
    assert auth_service._coerce_auth_subject(1) is None


def test_redact_auth_error_hides_common_secret_shapes() -> None:
    message = auth_service._redact_auth_error(
        "provider failed https://user:url-secret@example.test/oauth "
        "for user@example.com access_token=access-secret "
        "auth_token=auth-secret refresh_token=refresh-secret "
        "client_secret=client-secret authorization=Bearer bearer-secret",
    )

    assert "url-secret" not in message
    assert "access-secret" not in message
    assert "auth-secret" not in message
    assert "refresh-secret" not in message
    assert "client-secret" not in message
    assert "bearer-secret" not in message
    assert "user@example.com" not in message
    assert "https://***@example.test/oauth" in message
    assert "***@example.com" in message
    assert "access_token=***" in message
    assert "auth_token=***" in message
    assert "refresh_token=***" in message
    assert "client_secret=***" in message
    assert "authorization=Bearer ***" in message


def test_verify_password_fails_closed_for_malformed_hash() -> None:
    assert auth_service.AuthService._verify_password("secret", "not-a-bcrypt-hash") is False


def test_hash_password_rejects_bcrypt_oversized_passwords() -> None:
    with pytest.raises(auth_service.HTTPException) as exc_info:
        auth_service.AuthService._hash_password("a" * 73)

    assert exc_info.value.status_code == auth_service.status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Password must be 72 bytes or fewer"


def test_make_token_rejects_missing_user_id(monkeypatch) -> None:
    def fail_create_access_token(*args, **kwargs):
        raise AssertionError("missing user ids should not create tokens")

    monkeypatch.setattr(auth_service, "create_access_token", fail_create_access_token)

    with pytest.raises(auth_service.HTTPException) as exc_info:
        auth_service.AuthService._make_token(SimpleNamespace())

    assert exc_info.value.status_code == auth_service.status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc_info.value.detail == "Unable to create access token"


@pytest.mark.asyncio
async def test_signup_welcome_email_prefers_explicit_frontend_origin(monkeypatch) -> None:
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

    async def fake_get_user_by_email(self, email: str):
        return None

    async def fake_create_stripe_customer(self, email: str, name: str | None):
        return None

    monkeypatch.setattr(email_templates, "welcome", fake_welcome)
    monkeypatch.setattr(email_service.EmailService, "send_email", fake_send_email)
    monkeypatch.setattr(auth_service.AuthService, "_get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(
        auth_service.AuthService,
        "_create_stripe_customer",
        fake_create_stripe_customer,
    )
    monkeypatch.setattr(
        auth_service.AuthService,
        "_make_token",
        staticmethod(lambda user: "signup-token"),
    )
    service = auth_service.AuthService(_FakeDB())
    user, token = await service.signup(
        email="user@example.com",
        password="correct horse battery staple",
        name="Test User",
        frontend_origin=" https://app.example.com/ ",
    )

    assert token == "signup-token"
    assert user.email == "user@example.com"
    assert captured["name"] == "Test User"
    assert captured["dashboard_url"] == "https://app.example.com/dashboard"
    assert captured["to_email"] == "user@example.com"
    assert captured["subject"] == "Welcome"


@pytest.mark.asyncio
async def test_login_fails_closed_for_missing_legacy_password_hash(monkeypatch) -> None:
    async def fake_get_user_by_email(self, email: str):
        return SimpleNamespace(email=email)

    def fail_verify_password(*args, **kwargs) -> bool:
        raise AssertionError("malformed hashes should not be verified")

    monkeypatch.setattr(
        auth_service.AuthService,
        "_get_user_by_email",
        fake_get_user_by_email,
    )
    monkeypatch.setattr(
        auth_service.AuthService,
        "_verify_password",
        fail_verify_password,
    )

    service = auth_service.AuthService(_FakeDB())

    with pytest.raises(auth_service.HTTPException) as exc_info:
        await service.login("user@example.com", "secret")

    assert exc_info.value.status_code == auth_service.status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Invalid email or password"


@pytest.mark.asyncio
async def test_github_callback_uses_noreply_email_when_provider_email_missing(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class _GitHubDB(_FakeDB):
        async def execute(self, stmt):
            captured["lookup_stmt"] = stmt
            return _ScalarResult(None)

    def fake_decode_oauth_state(state: str, provider: str):
        captured["state"] = state
        captured["provider"] = provider
        return {"api_base_url": "https://api.example.com/proxy"}

    async def fake_exchange_github_code(code: str, redirect_uri: str | None = None):
        captured["code"] = code
        captured["redirect_uri"] = redirect_uri
        return {
            "id": "123",
            "email": None,
            "access_token": "gh-token",
            "name": "Octo User",
            "avatar_url": None,
        }

    async def fail_get_user_by_email(self, email: str):
        raise AssertionError("missing GitHub email should not query by email")

    async def fake_create_stripe_customer(self, email: str, name: str | None):
        captured["stripe_email"] = email
        captured["stripe_name"] = name
        return None

    monkeypatch.setattr(auth_service, "decode_oauth_state", fake_decode_oauth_state)
    monkeypatch.setattr(auth_service, "exchange_github_code", fake_exchange_github_code)
    monkeypatch.setattr(
        auth_service.AuthService,
        "_get_user_by_email",
        fail_get_user_by_email,
    )
    monkeypatch.setattr(
        auth_service.AuthService,
        "_create_stripe_customer",
        fake_create_stripe_customer,
    )
    monkeypatch.setattr(
        auth_service.AuthService,
        "_make_token",
        staticmethod(lambda user: "oauth-token"),
    )

    db = _GitHubDB()
    service = auth_service.AuthService(db)

    user, token = await service.github_callback("code", "state-token")

    assert token == "oauth-token"
    assert user.email == "gh-123@users.noreply.github.com"
    assert user in db.added
    assert captured["state"] == "state-token"
    assert captured["provider"] == "github"
    assert captured["code"] == "code"
    assert captured["redirect_uri"] == "https://api.example.com/proxy/auth/github/callback"
    assert captured["stripe_email"] == "gh-123@users.noreply.github.com"
    assert captured["stripe_name"] == "Octo User"


@pytest.mark.asyncio
async def test_github_callback_drops_unsafe_provider_avatar_url(monkeypatch) -> None:
    class _GitHubDB(_FakeDB):
        async def execute(self, stmt):
            return _ScalarResult(None)

    monkeypatch.setattr(
        auth_service,
        "decode_oauth_state",
        lambda state, provider: {"api_base_url": "https://api.example.com"},
    )

    async def fake_exchange_github_code(code: str, redirect_uri: str | None = None):
        return {
            "id": "123",
            "email": None,
            "access_token": "gh-token",
            "name": "Octo User",
            "avatar_url": "https://avatars.example/u/1?access_token=secret",
        }

    async def fake_create_stripe_customer(self, email: str, name: str | None):
        return None

    monkeypatch.setattr(auth_service, "exchange_github_code", fake_exchange_github_code)
    monkeypatch.setattr(
        auth_service.AuthService,
        "_create_stripe_customer",
        fake_create_stripe_customer,
    )
    monkeypatch.setattr(
        auth_service.AuthService,
        "_make_token",
        staticmethod(lambda user: "oauth-token"),
    )

    service = auth_service.AuthService(_GitHubDB())

    user, token = await service.github_callback("code", "state-token")

    assert token == "oauth-token"
    assert user.avatar_url is None


@pytest.mark.asyncio
async def test_google_callback_drops_unsafe_provider_avatar_url(monkeypatch) -> None:
    class _GoogleDB(_FakeDB):
        async def execute(self, stmt):
            return _ScalarResult(None)

    monkeypatch.setattr(
        auth_service,
        "decode_oauth_state",
        lambda state, provider: {"api_base_url": "https://api.example.com"},
    )

    async def fake_get_user_by_email(self, email: str):
        return None

    async def fake_exchange_google_code(code: str, redirect_uri: str | None = None):
        return {
            "id": "google-1",
            "email": "user@example.com",
            "name": "Google User",
            "avatar_url": "https://avatars.example/u/1#client_secret=secret",
            "access_token": "google-token",
        }

    async def fake_create_stripe_customer(self, email: str, name: str | None):
        return None

    monkeypatch.setattr(auth_service, "exchange_google_code", fake_exchange_google_code)
    monkeypatch.setattr(
        auth_service.AuthService,
        "_get_user_by_email",
        fake_get_user_by_email,
    )
    monkeypatch.setattr(
        auth_service.AuthService,
        "_create_stripe_customer",
        fake_create_stripe_customer,
    )
    monkeypatch.setattr(
        auth_service.AuthService,
        "_make_token",
        staticmethod(lambda user: "oauth-token"),
    )

    service = auth_service.AuthService(_GoogleDB())

    user, token = await service.google_callback("code", "state-token")

    assert token == "oauth-token"
    assert user.avatar_url is None


@pytest.mark.asyncio
async def test_request_password_reset_skips_malformed_legacy_user(monkeypatch) -> None:
    async def fake_get_user_by_email(self, email: str):
        return SimpleNamespace(id=None, email=["user@example.com"])

    def fail_create_access_token(*args, **kwargs):
        raise AssertionError("malformed reset users should not create tokens")

    monkeypatch.setattr(
        auth_service.AuthService,
        "_get_user_by_email",
        fake_get_user_by_email,
    )
    monkeypatch.setattr(auth_service, "create_access_token", fail_create_access_token)

    service = auth_service.AuthService(_FakeDB())

    await service.request_password_reset("user@example.com")


@pytest.mark.asyncio
async def test_request_password_reset_marks_reset_token_purpose(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_user_by_email(self, email: str):
        return SimpleNamespace(id="user-1", email="user@example.com")

    def fake_create_access_token(user_id: str, **kwargs):
        captured["user_id"] = user_id
        captured.update(kwargs)
        return "reset-token"

    class _FakeEmailService:
        async def send_password_reset(
            self,
            email: str,
            token: str,
            *,
            frontend_origin: str | None = None,
        ) -> bool:
            captured["email"] = email
            captured["token"] = token
            captured["frontend_origin"] = frontend_origin
            return True

    monkeypatch.setattr(
        auth_service.AuthService,
        "_get_user_by_email",
        fake_get_user_by_email,
    )
    monkeypatch.setattr(auth_service, "create_access_token", fake_create_access_token)
    monkeypatch.setattr(email_service, "EmailService", _FakeEmailService)

    service = auth_service.AuthService(_FakeDB())

    await service.request_password_reset(
        "user@example.com",
        frontend_origin="https://app.example.com",
    )

    assert captured["user_id"] == "user-1"
    assert captured["expires_delta"] == auth_service.timedelta(hours=1)
    assert captured["extra_claims"] == {"purpose": "password_reset"}
    assert captured["email"] == "user@example.com"
    assert captured["token"] == "reset-token"
    assert captured["frontend_origin"] == "https://app.example.com"


@pytest.mark.asyncio
async def test_request_password_reset_redacts_email_failure_logs(
    monkeypatch,
    caplog,
) -> None:
    async def fake_get_user_by_email(self, email: str):
        return SimpleNamespace(id="user-1", email="user@example.com")

    def fake_create_access_token(user_id: str, **kwargs):
        return "reset-token"

    class _FailingEmailService:
        async def send_password_reset(
            self,
            email: str,
            token: str,
            *,
            frontend_origin: str | None = None,
        ) -> bool:
            raise RuntimeError(
                f"SMTP rejected {email} via https://user:secret@example.test/reset "
                "access_token=abc123"
            )

    monkeypatch.setattr(
        auth_service.AuthService,
        "_get_user_by_email",
        fake_get_user_by_email,
    )
    monkeypatch.setattr(auth_service, "create_access_token", fake_create_access_token)
    monkeypatch.setattr(email_service, "EmailService", _FailingEmailService)
    caplog.set_level(logging.WARNING, logger="codey")

    service = auth_service.AuthService(_FakeDB())

    await service.request_password_reset("user@example.com")

    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/reset" in caplog.text
    assert "access_token=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_reset_password_rejects_generic_access_token_before_db(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_service,
        "decode_access_token",
        lambda token: {"sub": "user-1", "exp": 1},
    )

    class _FailingDB(_FakeDB):
        async def execute(self, *args, **kwargs):
            raise AssertionError("generic access tokens should not query users")

    service = auth_service.AuthService(_FailingDB())

    assert await service.reset_password("token", "new-password") is False


@pytest.mark.asyncio
async def test_reset_password_converts_decode_failure_to_false(monkeypatch) -> None:
    def fake_decode_access_token(token: str):
        raise auth_service.HTTPException(
            status_code=auth_service.status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    monkeypatch.setattr(auth_service, "decode_access_token", fake_decode_access_token)

    class _FailingDB(_FakeDB):
        async def execute(self, *args, **kwargs):
            raise AssertionError("invalid reset tokens should not query users")

    service = auth_service.AuthService(_FailingDB())

    assert await service.reset_password("token", "new-password") is False


@pytest.mark.asyncio
async def test_reset_password_rejects_malformed_subject_before_db(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_service,
        "decode_access_token",
        lambda token: {"purpose": "password_reset", "sub": ["user-1"]},
    )

    service = auth_service.AuthService(_FakeDB())

    assert await service.reset_password("token", "new-password") is False
