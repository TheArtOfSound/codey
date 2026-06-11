from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException, status

import codey.saas.auth.jwt as auth_jwt
import codey.saas.auth.oauth as auth_oauth


def test_decode_access_token_rejects_non_object_payload(monkeypatch) -> None:
    monkeypatch.setattr(auth_jwt.jwt, "decode", lambda *args, **kwargs: "oops")

    with pytest.raises(HTTPException) as exc_info:
        auth_jwt.decode_access_token("token")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Invalid or expired token"


def test_create_access_token_rejects_whitespace_only_subject() -> None:
    with pytest.raises(ValueError, match="subject"):
        auth_jwt.create_access_token("   ")


def test_create_access_token_rejects_control_character_subject() -> None:
    with pytest.raises(ValueError, match="subject"):
        auth_jwt.create_access_token("user-1\tadmin")


def test_create_access_token_trims_subject() -> None:
    token = auth_jwt.create_access_token(" user-1 ")

    payload = auth_jwt.decode_access_token(token)

    assert payload["sub"] == "user-1"


def test_create_access_token_honors_zero_expiry_delta(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_encode(payload, secret_key, algorithm):
        captured["payload"] = payload
        captured["secret_key"] = secret_key
        captured["algorithm"] = algorithm
        return "encoded"

    monkeypatch.setattr(auth_jwt.jwt, "encode", fake_encode)

    token = auth_jwt.create_access_token("user-1", expires_delta=timedelta(0))

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert token == "encoded"
    assert payload["exp"] == payload["iat"]


def test_create_access_token_adds_extra_claims_without_overriding_reserved_claims(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_encode(payload, secret_key, algorithm):
        captured["payload"] = payload
        return "encoded"

    monkeypatch.setattr(auth_jwt.jwt, "encode", fake_encode)

    token = auth_jwt.create_access_token(
        "user-1",
        extra_claims={
            "purpose": "password_reset",
            "sub": "attacker",
            "exp": "never",
            "iat": "later",
        },
    )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert token == "encoded"
    assert payload["sub"] == "user-1"
    assert payload["purpose"] == "password_reset"
    assert payload["exp"] != "never"
    assert payload["iat"] != "later"


def test_decode_access_token_trims_whitespace_padded_token() -> None:
    token = auth_jwt.create_access_token("user-1")

    payload = auth_jwt.decode_access_token(f"  {token}  ")

    assert payload["sub"] == "user-1"


def test_decode_access_token_rejects_whitespace_only_token() -> None:
    with pytest.raises(HTTPException) as exc_info:
        auth_jwt.decode_access_token("   ")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Invalid or expired token"


def test_normalize_access_token_candidate_rejects_ascii_controls() -> None:
    assert auth_jwt.normalize_access_token_candidate(" token ") == "token"
    assert auth_jwt.normalize_access_token_candidate("token\tbad") is None
    assert auth_jwt.normalize_access_token_candidate("token\x7fbad") is None


def test_decode_access_token_rejects_blank_subject(monkeypatch) -> None:
    monkeypatch.setattr(auth_jwt.jwt, "decode", lambda *args, **kwargs: {"sub": "   "})

    with pytest.raises(HTTPException) as exc_info:
        auth_jwt.decode_access_token("token")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Token missing subject claim"


def test_decode_access_token_rejects_missing_expiration(monkeypatch) -> None:
    monkeypatch.setattr(auth_jwt.jwt, "decode", lambda *args, **kwargs: {"sub": "user-1"})

    with pytest.raises(HTTPException) as exc_info:
        auth_jwt.decode_access_token("token")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Token missing expiration claim"


@pytest.mark.parametrize("expiration", ["never", True, -1, 1.5])
def test_decode_access_token_rejects_malformed_expiration(monkeypatch, expiration) -> None:
    monkeypatch.setattr(
        auth_jwt.jwt,
        "decode",
        lambda *args, **kwargs: {"sub": "user-1", "exp": expiration},
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_jwt.decode_access_token("token")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Invalid or expired token"


def test_decode_oauth_state_rejects_non_object_payload(monkeypatch) -> None:
    monkeypatch.setattr(auth_oauth.jwt, "decode", lambda *args, **kwargs: ["oops"])

    with pytest.raises(ValueError, match="Invalid OAuth state"):
        auth_oauth.decode_oauth_state("token", "github")


def test_decode_oauth_state_rejects_missing_required_claims(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_oauth.jwt,
        "decode",
        lambda *args, **kwargs: {
            "purpose": "oauth_state",
            "provider": "github",
            "nonce": "nonce",
        },
    )

    with pytest.raises(ValueError, match="Invalid OAuth state"):
        auth_oauth.decode_oauth_state("token", "github")


def test_decode_oauth_state_rejects_blank_nonce(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_oauth.jwt,
        "decode",
        lambda *args, **kwargs: {
            "purpose": "oauth_state",
            "provider": "github",
            "nonce": "   ",
            "iat": 1,
            "exp": 2,
        },
    )

    with pytest.raises(ValueError, match="Invalid OAuth state"):
        auth_oauth.decode_oauth_state("token", "github")


def test_decode_oauth_state_rejects_non_numeric_timestamps(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_oauth.jwt,
        "decode",
        lambda *args, **kwargs: {
            "purpose": "oauth_state",
            "provider": "github",
            "nonce": "nonce",
            "iat": "1",
            "exp": "2",
        },
    )

    with pytest.raises(ValueError, match="Invalid OAuth state"):
        auth_oauth.decode_oauth_state("token", "github")


def test_decode_oauth_state_rejects_non_positive_lifetime(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_oauth.jwt,
        "decode",
        lambda *args, **kwargs: {
            "purpose": "oauth_state",
            "provider": "github",
            "nonce": "nonce",
            "iat": 2,
            "exp": 2,
        },
    )

    with pytest.raises(ValueError, match="Invalid OAuth state"):
        auth_oauth.decode_oauth_state("token", "github")


def test_decode_oauth_state_normalizes_valid_claims(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_oauth.jwt,
        "decode",
        lambda *args, **kwargs: {
            "purpose": "oauth_state",
            "provider": "github",
            "nonce": " nonce ",
            "iat": 1.0,
            "exp": 2.0,
        },
    )

    payload = auth_oauth.decode_oauth_state("token", "github")

    assert payload["nonce"] == "nonce"
    assert payload["iat"] == 1
    assert payload["exp"] == 2


def test_decode_oauth_state_trims_whitespace_padded_token() -> None:
    _, state = auth_oauth.oauth_github_url()

    payload = auth_oauth.decode_oauth_state(f"  {state}  ", "github")

    assert payload["purpose"] == "oauth_state"
    assert payload["provider"] == "github"


def test_decode_oauth_state_rejects_whitespace_only_token() -> None:
    with pytest.raises(ValueError, match="Invalid or expired OAuth state"):
        auth_oauth.decode_oauth_state("   ", "github")
