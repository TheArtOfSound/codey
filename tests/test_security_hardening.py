import uuid
from urllib.parse import parse_qs, urlparse

import pytest

from codey.saas.auth.oauth import (
    oauth_github_url,
    oauth_google_url,
    validate_oauth_state,
)
from codey.saas.models.coding_session import CodingSession
from codey.saas.models.user import User


def test_github_oauth_url_includes_signed_state() -> None:
    url, state = oauth_github_url()
    query = parse_qs(urlparse(url).query)

    assert query["state"][0] == state
    validate_oauth_state(state, "github")


def test_google_state_rejects_wrong_provider() -> None:
    _, state = oauth_google_url()

    with pytest.raises(ValueError):
        validate_oauth_state(state, "github")


def test_github_token_is_encrypted_at_rest() -> None:
    user = User(email="tester@example.com")
    user.github_token = "ghp_secret_token"

    assert user._github_token_ciphertext is not None
    assert user._github_token_ciphertext != "ghp_secret_token"
    assert user.github_token == "ghp_secret_token"


def test_legacy_plaintext_github_tokens_still_read() -> None:
    user = User(email="legacy@example.com")
    user._github_token_ciphertext = "legacy-plaintext-token"

    assert user.github_token == "legacy-plaintext-token"


def test_github_token_fails_closed_for_blank_and_normalizes_legacy_padding() -> None:
    user = User(email="blank@example.com")
    user.github_token = "   "

    assert user._github_token_ciphertext is None
    assert user.github_token is None

    legacy_user = User(email="legacy-whitespace@example.com")
    legacy_user._github_token_ciphertext = " legacy-plaintext-token "

    assert legacy_user.github_token == "legacy-plaintext-token"


def test_coding_session_output_alias_maps_to_output_summary() -> None:
    session = CodingSession(user_id=uuid.uuid4(), mode="test")
    session.output = "generated output"

    assert session.output_summary == "generated output"
    assert session.output == "generated output"
