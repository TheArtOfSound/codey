from __future__ import annotations

from datetime import datetime, timedelta, timezone

from codey.saas.models.api_key import ApiKey


def test_api_key_is_expired_handles_string_timestamps() -> None:
    future_key = ApiKey(expires_at="2999-01-01T00:00:00Z")
    past_key = ApiKey(expires_at="2000-01-01T00:00:00Z")

    assert future_key.is_expired is False
    assert past_key.is_expired is True


def test_api_key_is_expired_handles_timezone_aware_datetimes() -> None:
    future_key = ApiKey(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    past_key = ApiKey(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))

    assert future_key.is_expired is False
    assert past_key.is_expired is True


def test_api_key_is_expired_fails_closed_for_invalid_strings() -> None:
    api_key = ApiKey(expires_at="not-a-timestamp")

    assert api_key.is_expired is True
