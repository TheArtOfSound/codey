from __future__ import annotations

import pytest
from pydantic import ValidationError

import codey.saas.api.auth_routes as auth_routes


def test_signup_request_rejects_blank_passwords() -> None:
    with pytest.raises(ValidationError):
        auth_routes.SignupRequest(
            email="user@example.com",
            password="   ",
        )


def test_signup_request_rejects_bcrypt_oversized_passwords() -> None:
    with pytest.raises(ValidationError):
        auth_routes.SignupRequest(
            email="user@example.com",
            password="a" * 73,
        )


def test_signup_request_normalizes_blank_names_to_none() -> None:
    request = auth_routes.SignupRequest(
        email="user@example.com",
        password="correct horse battery staple",
        name="   ",
    )

    assert request.name is None


def test_login_request_rejects_blank_passwords() -> None:
    with pytest.raises(ValidationError):
        auth_routes.LoginRequest(
            email="user@example.com",
            password="   ",
        )


def test_reset_password_confirm_request_rejects_blank_fields() -> None:
    with pytest.raises(ValidationError):
        auth_routes.ResetPasswordConfirmRequest(token="   ", password="new-password")

    with pytest.raises(ValidationError):
        auth_routes.ResetPasswordConfirmRequest(token="reset-token", password="   ")


def test_reset_password_confirm_request_rejects_bcrypt_oversized_passwords() -> None:
    with pytest.raises(ValidationError):
        auth_routes.ResetPasswordConfirmRequest(
            token="reset-token",
            password="a" * 73,
        )
