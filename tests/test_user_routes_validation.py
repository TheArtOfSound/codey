from __future__ import annotations

import pytest
from pydantic import ValidationError

import codey.saas.api.user_routes as user_routes


def test_connect_github_token_request_rejects_blank_tokens() -> None:
    with pytest.raises(ValidationError):
        user_routes.ConnectGitHubTokenRequest(token=" " * 20)


def test_connect_github_token_request_rejects_line_break_tokens() -> None:
    with pytest.raises(ValidationError):
        user_routes.ConnectGitHubTokenRequest(token="ghp_1234567890\nInjected: header")


def test_connect_github_token_request_rejects_ascii_control_tokens() -> None:
    with pytest.raises(ValidationError):
        user_routes.ConnectGitHubTokenRequest(token="ghp_1234567890\tInjected")


def test_connect_github_token_request_rejects_internal_whitespace_tokens() -> None:
    with pytest.raises(ValidationError):
        user_routes.ConnectGitHubTokenRequest(token="ghp_1234567890 bad")


def test_connect_github_token_request_strips_valid_tokens() -> None:
    request = user_routes.ConnectGitHubTokenRequest(token="  ghp_12345678901234567890  ")

    assert request.token == "ghp_12345678901234567890"


def test_change_password_request_rejects_blank_current_passwords() -> None:
    with pytest.raises(ValidationError):
        user_routes.ChangePasswordRequest(
            current_password="   ",
            new_password="valid-password",
        )


def test_change_password_request_rejects_blank_new_passwords() -> None:
    with pytest.raises(ValidationError):
        user_routes.ChangePasswordRequest(
            current_password="current-password",
            new_password="        ",
        )


def test_change_password_request_rejects_bcrypt_oversized_new_passwords() -> None:
    with pytest.raises(ValidationError):
        user_routes.ChangePasswordRequest(
            current_password="current-password",
            new_password="a" * 73,
        )


def test_change_password_request_preserves_password_whitespace() -> None:
    request = user_routes.ChangePasswordRequest(
        current_password=" current-password ",
        new_password=" new-password ",
    )

    assert request.current_password == " current-password "
    assert request.new_password == " new-password "


def test_create_api_key_request_rejects_blank_names() -> None:
    with pytest.raises(ValidationError):
        user_routes.CreateApiKeyRequest(name="   ")


def test_update_user_request_rejects_blank_names() -> None:
    with pytest.raises(ValidationError):
        user_routes.UpdateUserRequest(name="   ")


def test_update_user_request_strips_valid_avatar_urls() -> None:
    request = user_routes.UpdateUserRequest(
        avatar_url=" https://avatars.example.com/u/1?v=4 "
    )

    assert request.avatar_url == "https://avatars.example.com/u/1?v=4"


@pytest.mark.parametrize(
    "avatar_url",
    [
        "   ",
        "javascript:alert(1)",
        "https://user:secret@avatars.example.com/u/1",
        "https://avatars.example.com/u/1?access_token=secret",
        "https://avatars.example.com/u/1#client_secret=secret",
        "https://avatars.example.com:not-a-port/u/1",
        "https:///u/1",
        "https://avatars.example.com/u/1\r\nbad",
    ],
)
def test_update_user_request_rejects_unsafe_avatar_urls(avatar_url: str) -> None:
    with pytest.raises(ValidationError):
        user_routes.UpdateUserRequest(avatar_url=avatar_url)
