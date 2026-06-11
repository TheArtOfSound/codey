from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

import codey.saas.api.referral_routes as referral_routes
import codey.saas.auth.public_urls as public_urls


def test_referral_to_entry_tolerates_string_timestamps() -> None:
    referral = SimpleNamespace(
        id="referral-1",
        status="converted",
        created_at=" 2026-01-02T03:04:05Z ",
        converted_at="2026-01-03T03:04:05Z",
        credits_issued_referrer=25,
    )

    response = referral_routes._referral_to_entry(referral, None)

    assert response.email == "Pending invite"
    assert response.invited_at == "2026-01-02T03:04:05Z"
    assert response.converted_at == "2026-01-03T03:04:05Z"


def test_referral_to_entry_normalizes_malformed_fields() -> None:
    referral = SimpleNamespace(
        id="referral-2",
        status=["converted"],
        created_at="2026-01-02T03:04:05Z",
        converted_at=None,
        credits_issued_referrer=" 25 ",
    )

    response = referral_routes._referral_to_entry(
        referral,
        {"email": "friend@example.com"},
    )

    assert response.email == "Pending invite"
    assert response.status == "pending"
    assert response.credits_earned == 25


def test_referral_to_entry_rejects_control_character_text_fields() -> None:
    referral = SimpleNamespace(
        id="referral-5",
        status="con\nverted",
        created_at="2026-01-02T03:04:05Z\tlegacy",
        converted_at="2026-01-03T03:04:05Z\x7flegacy",
        credits_issued_referrer=25,
    )

    response = referral_routes._referral_to_entry(
        referral,
        "friend\n@example.com",
    )

    assert response.email == "Pending invite"
    assert response.status == "pending"
    assert response.invited_at == ""
    assert response.converted_at is None


def test_referral_to_entry_tolerates_missing_legacy_fields() -> None:
    response = referral_routes._referral_to_entry(
        SimpleNamespace(id="referral-4"),
        None,
    )

    assert response.id == "referral-4"
    assert response.email == "Pending invite"
    assert response.status == "pending"
    assert response.invited_at == ""
    assert response.converted_at is None
    assert response.credits_earned == 0


def test_referral_status_helper_normalizes_malformed_status() -> None:
    assert (
        referral_routes._coerce_referral_status(SimpleNamespace(status="converted"))
        == "converted"
    )
    assert (
        referral_routes._coerce_referral_status(SimpleNamespace(status=["converted"]))
        == "pending"
    )
    assert referral_routes._coerce_referral_status(SimpleNamespace()) == "pending"


def test_referral_history_row_parser_skips_malformed_rows() -> None:
    referral = SimpleNamespace(id="referral-1")

    assert referral_routes._coerce_referral_history_row(
        (referral, "friend@example.com")
    ) == (referral, "friend@example.com")
    assert referral_routes._coerce_referral_history_row((referral,)) is None
    assert referral_routes._coerce_referral_history_row(("bad-referral", None)) is None
    assert referral_routes._coerce_referral_history_row(None) is None


def test_referral_row_list_coercion_rejects_malformed_results() -> None:
    row = (SimpleNamespace(id="referral-1"), "friend@example.com")

    assert referral_routes._coerce_referral_row_list([row]) == [row]
    assert referral_routes._coerce_referral_row_list((row,)) == [row]
    assert referral_routes._coerce_referral_row_list(None) == []
    assert referral_routes._coerce_referral_row_list("bad") == []


def test_claim_to_response_normalizes_malformed_status() -> None:
    referral = SimpleNamespace(
        id="referral-3",
        status=["claimed"],
    )

    response = referral_routes._claim_to_response(referral)

    assert response.status == "pending"
    assert response.referral_id == "referral-3"


def test_claim_to_response_tolerates_missing_legacy_fields() -> None:
    response = referral_routes._claim_to_response(SimpleNamespace())

    assert response.status == "pending"
    assert response.referral_id == ""


def test_stats_to_response_coerces_malformed_fields() -> None:
    response = referral_routes._stats_to_response(
        referral_link=["https://example.com/signup?ref=user-1"],
        total_referrals="4",
        pending={"count": 2},
        converted=" 3 ",
        total_credits_earned=["25"],
    )

    assert response.referral_link == ""
    assert response.total_referrals == 4
    assert response.pending == 0
    assert response.converted == 3
    assert response.total_credits_earned == 0


def test_stats_to_response_rejects_control_character_text_fields() -> None:
    response = referral_routes._stats_to_response(
        referral_link="https://example.com/signup\n?ref=user-1",
        total_referrals=1,
        pending=0,
        converted=1,
        total_credits_earned=5,
    )

    assert response.referral_link == ""


def test_referral_int_coercion_rejects_malformed_values() -> None:
    assert referral_routes._coerce_referral_int(True, fallback=-1) == -1
    assert referral_routes._coerce_referral_int(float("nan"), fallback=-1) == -1
    assert referral_routes._coerce_referral_int(float("inf"), fallback=-1) == -1
    assert referral_routes._coerce_referral_int("3", fallback=-1) == 3


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _StatsDB:
    def __init__(self, values):
        self._values = iter(values)

    async def execute(self, _statement):
        return _ScalarResult(next(self._values))


@pytest.mark.asyncio
async def test_get_referral_stats_uses_normalized_frontend_origin(monkeypatch) -> None:
    user_id = uuid4()
    db = _StatsDB([4, 2, 1, 25])
    request = Request({"type": "http", "headers": []})

    monkeypatch.setattr(public_urls.settings, "frontend_url", "   ")

    response = await referral_routes.get_referral_stats(
        request=request,
        current_user=SimpleNamespace(id=user_id),
        db=db,
    )

    assert response.referral_link == f"/auth/signup?ref={user_id}"
    assert response.total_referrals == 4
    assert response.pending == 2
    assert response.converted == 1
    assert response.total_credits_earned == 25


@pytest.mark.asyncio
async def test_get_referral_stats_url_encodes_referrer_id(monkeypatch) -> None:
    db = _StatsDB([4, 2, 1, 25])
    request = Request({"type": "http", "headers": []})

    monkeypatch.setattr(public_urls.settings, "frontend_url", "   ")

    response = await referral_routes.get_referral_stats(
        request=request,
        current_user=SimpleNamespace(id="user-1&next=https://evil.example"),
        db=db,
    )

    assert response.referral_link == (
        "/auth/signup?ref=user-1%26next%3Dhttps%3A%2F%2Fevil.example"
    )


@pytest.mark.asyncio
async def test_get_referral_stats_prefers_request_frontend_origin_header() -> None:
    user_id = uuid4()
    db = _StatsDB([4, 2, 1, 25])
    request = Request(
        {
            "type": "http",
            "headers": [
                (
                    public_urls.FRONTEND_ORIGIN_HEADER.encode("latin-1"),
                    b" https://app.example.com ",
                ),
            ],
        }
    )

    response = await referral_routes.get_referral_stats(
        request=request,
        current_user=SimpleNamespace(id=user_id),
        db=db,
    )

    assert (
        response.referral_link
        == f"https://app.example.com/auth/signup?ref={user_id}"
    )


@pytest.mark.asyncio
async def test_get_referral_stats_alias_uses_configured_frontend_origin(
    monkeypatch,
) -> None:
    user_id = uuid4()
    db = _StatsDB([4, 2, 1, 25])

    monkeypatch.setattr(
        public_urls.settings,
        "frontend_url",
        " https://app.example.com ",
    )

    response = await referral_routes.get_referral_stats_alias(
        current_user=SimpleNamespace(id=user_id),
        db=db,
    )

    assert (
        response.referral_link
        == f"https://app.example.com/auth/signup?ref={user_id}"
    )
    assert response.total_referrals == 4
    assert response.pending == 2
    assert response.converted == 1
    assert response.total_credits_earned == 25
