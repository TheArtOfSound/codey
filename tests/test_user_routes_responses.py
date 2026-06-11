from __future__ import annotations

from types import SimpleNamespace

import codey.saas.api.user_routes as user_routes


def test_api_key_to_response_tolerates_string_timestamps() -> None:
    api_key = SimpleNamespace(
        id="key-1",
        name="Primary",
        key_prefix="ck_123",
        created_at=" 2026-01-02T03:04:05Z ",
        last_used="2026-01-03T03:04:05Z",
        expires_at=None,
        is_expired=False,
    )

    response = user_routes._api_key_to_response(api_key)

    assert response.created_at == "2026-01-02T03:04:05Z"
    assert response.last_used_at == "2026-01-03T03:04:05Z"
    assert response.expires_at is None


def test_api_key_to_response_normalizes_malformed_fields() -> None:
    api_key = SimpleNamespace(
        id="key-1",
        name=["Primary"],
        key_prefix={"prefix": "ck_123"},
        created_at="2026-01-02T03:04:05Z",
        last_used=None,
        expires_at=None,
        is_expired=" yes ",
    )

    response = user_routes._api_key_to_response(api_key)

    assert response.name is None
    assert response.key_prefix is None
    assert response.is_expired is True


def test_api_key_to_response_tolerates_missing_legacy_fields() -> None:
    response = user_routes._api_key_to_response(SimpleNamespace(id="key-2"))

    assert response.id == "key-2"
    assert response.name is None
    assert response.key_prefix is None
    assert response.created_at == ""
    assert response.last_used_at is None
    assert response.expires_at is None
    assert response.is_expired is False


def test_user_to_profile_response_tolerates_string_timestamps() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="User",
        avatar_url=None,
        github_id=None,
        github_token=None,
        plan="free",
        plan_display_name="Free",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        credits_used_this_month=0,
        subscription_period_end=" 2026-02-01T00:00:00Z ",
        created_at="2026-01-01T00:00:00Z",
        last_active="2026-01-05T00:00:00Z",
    )

    response = user_routes._user_to_profile_response(user)

    assert response.subscription_period_end == "2026-02-01T00:00:00Z"
    assert response.created_at == "2026-01-01T00:00:00Z"
    assert response.last_active == "2026-01-05T00:00:00Z"


def test_user_to_profile_response_coerces_malformed_plan_fields() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="User",
        avatar_url=None,
        github_id=None,
        github_token=None,
        plan=["pro"],
        plan_display_name=["Pro"],
        plan_status={"state": "active"},
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        credits_used_this_month=0,
        subscription_period_end=None,
        created_at="2026-01-01T00:00:00Z",
        last_active=None,
    )

    response = user_routes._user_to_profile_response(user)

    assert response.plan == "free"
    assert response.plan_display_name == "Free"
    assert response.plan_status == "active"
    assert response.monthly_allocation == 10


def test_user_to_profile_response_uses_billing_plan_display_name_fallback() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="User",
        avatar_url=None,
        github_id=None,
        github_token=None,
        plan="starter",
        plan_display_name={"name": "Starter"},
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        credits_used_this_month=0,
        subscription_period_end=None,
        created_at="2026-01-01T00:00:00Z",
        last_active=None,
    )

    response = user_routes._user_to_profile_response(user)

    assert response.plan_display_name == user_routes.PLANS["starter"]["name"]


def test_user_to_profile_response_coerces_malformed_profile_and_credit_fields() -> None:
    user = SimpleNamespace(
        id="user-1",
        email=["user@example.com"],
        name=["User"],
        avatar_url={"url": "https://example.com/avatar.png"},
        github_id=None,
        github_token=None,
        plan="pro",
        plan_display_name="Pro",
        plan_status="active",
        credits_remaining="10",
        topup_credits={"value": 2},
        total_credits=["12"],
        credits_used_this_month="3",
        subscription_period_end=None,
        created_at="2026-01-01T00:00:00Z",
        last_active=None,
    )

    response = user_routes._user_to_profile_response(user)

    assert response.email == ""
    assert response.name is None
    assert response.avatar_url is None
    assert response.credits_remaining == 10
    assert response.topup_credits == 0
    assert response.total_credits == 10
    assert response.credits_used_this_month == 3


def test_user_to_profile_response_allows_safe_avatar_url_with_query() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="User",
        avatar_url=" https://avatars.example.com/u/1?v=4 ",
        github_id=None,
        github_token=None,
        plan="pro",
        plan_display_name="Pro",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        credits_used_this_month=3,
        subscription_period_end=None,
        created_at="2026-01-01T00:00:00Z",
        last_active=None,
    )

    response = user_routes._user_to_profile_response(user)

    assert response.avatar_url == "https://avatars.example.com/u/1?v=4"


def test_user_to_profile_response_rejects_unsafe_avatar_urls() -> None:
    unsafe_urls = [
        "javascript:alert(1)",
        "https://user:secret@avatars.example.com/u/1",
        "https://avatars.example.com/u/1?access_token=secret",
        "https://avatars.example.com/u/1#client_secret=secret",
        "https://avatars.example.com:not-a-port/u/1",
        "https:///u/1",
        "https://avatars.example.com/u/1\r\nbad",
    ]

    for avatar_url in unsafe_urls:
        user = SimpleNamespace(
            id="user-1",
            email="user@example.com",
            name="User",
            avatar_url=avatar_url,
            github_id=None,
            github_token=None,
            plan="pro",
            plan_display_name="Pro",
            plan_status="active",
            credits_remaining=10,
            topup_credits=0,
            total_credits=10,
            credits_used_this_month=3,
            subscription_period_end=None,
            created_at="2026-01-01T00:00:00Z",
            last_active=None,
        )

        response = user_routes._user_to_profile_response(user)

        assert response.avatar_url is None


def test_user_to_profile_response_tolerates_missing_legacy_fields() -> None:
    response = user_routes._user_to_profile_response(SimpleNamespace(id="user-2"))

    assert response.id == "user-2"
    assert response.email == ""
    assert response.name is None
    assert response.avatar_url is None
    assert response.plan == "free"
    assert response.plan_status == "active"
    assert response.credits_remaining == 0
    assert response.topup_credits == 0
    assert response.total_credits == 0
    assert response.credits_used_this_month == 0
    assert response.created_at == ""
    assert response.last_active is None


def test_user_to_profile_response_ignores_malformed_github_connection_fields() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="User",
        avatar_url=None,
        github_id={"id": "123"},
        github_token=["gh-token"],
        plan="pro",
        plan_display_name="Pro",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        credits_used_this_month=0,
        subscription_period_end=None,
        created_at="2026-01-01T00:00:00Z",
        last_active=None,
    )

    response = user_routes._user_to_profile_response(user)

    assert response.github_connected is False


def test_user_to_profile_response_rejects_line_break_github_tokens() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        name="User",
        avatar_url=None,
        github_id=None,
        github_token="ghp_validprefix\nInjected: header",
        plan="pro",
        plan_display_name="Pro",
        plan_status="active",
        credits_remaining=10,
        topup_credits=0,
        total_credits=10,
        credits_used_this_month=0,
        subscription_period_end=None,
        created_at="2026-01-01T00:00:00Z",
        last_active=None,
    )

    response = user_routes._user_to_profile_response(user)

    assert response.github_connected is False


def test_user_numeric_coercion_rejects_non_finite_values() -> None:
    assert user_routes._coerce_user_int(float("nan"), fallback=-1) == -1
    assert user_routes._coerce_user_int(float("inf"), fallback=-1) == -1
    assert user_routes._coerce_user_int("-inf", fallback=-1) == -1
    assert user_routes._coerce_user_int("3", fallback=-1) == 3
    assert user_routes._coerce_user_float(float("nan")) is None
    assert user_routes._coerce_user_float("inf") is None
    assert user_routes._coerce_user_float("0.42") == 0.42
    assert user_routes._coerce_user_bool(float("nan"), fallback=False) is False
    assert user_routes._coerce_user_bool(float("inf"), fallback=False) is False
    assert user_routes._coerce_user_bool(1, fallback=False) is True


def test_verify_user_password_fails_closed_for_malformed_hash(monkeypatch) -> None:
    def fail_checkpw(*args, **kwargs):
        raise ValueError("invalid salt")

    monkeypatch.setattr(user_routes.bcrypt, "checkpw", fail_checkpw)

    assert user_routes._verify_user_password("secret", "not-a-bcrypt-hash") is False


def test_user_row_list_coercion_rejects_malformed_results() -> None:
    row = SimpleNamespace(id="row-1")

    assert user_routes._coerce_user_row_list([row]) == [row]
    assert user_routes._coerce_user_row_list((row,)) == [row]
    assert user_routes._coerce_user_row_list(None) == []
    assert user_routes._coerce_user_row_list("bad") == []


def test_session_to_summary_tolerates_string_timestamps() -> None:
    session = SimpleNamespace(
        id="session-1",
        mode="autonomous",
        prompt="Fix deployment",
        repo_connected="octo/repo",
        status="completed",
        credits_charged=5,
        lines_generated=42,
        files_modified=3,
        nfet_phase_before="build",
        nfet_phase_after="validate",
        es_score_before=0.1,
        es_score_after=0.2,
        output_summary="Completed successfully",
        error_message=None,
        started_at=" 2026-01-04T03:04:05Z ",
        completed_at="2026-01-04T03:09:05Z",
    )

    response = user_routes._session_to_summary(session)

    assert response.started_at == "2026-01-04T03:04:05Z"
    assert response.completed_at == "2026-01-04T03:09:05Z"


def test_session_to_summary_coerces_malformed_text_fields() -> None:
    session = SimpleNamespace(
        id="session-1",
        mode=["autonomous"],
        prompt={"task": "Fix deployment"},
        repo_connected=["octo/repo"],
        status={"state": "completed"},
        credits_charged=5,
        lines_generated=42,
        files_modified=3,
        nfet_phase_before=["build"],
        nfet_phase_after={"name": "validate"},
        es_score_before=0.1,
        es_score_after=0.2,
        output_summary=["Completed successfully"],
        error_message={"oops": True},
        started_at="2026-01-04T03:04:05Z",
        completed_at=None,
    )

    response = user_routes._session_to_summary(session)

    assert response.mode == "unknown"
    assert response.prompt is None
    assert response.repo_connected is None
    assert response.status == "unknown"
    assert response.nfet_phase_before is None
    assert response.nfet_phase_after is None
    assert response.output_summary is None
    assert response.error_message is None


def test_session_to_summary_coerces_malformed_numeric_fields() -> None:
    session = SimpleNamespace(
        id="session-1",
        mode="autonomous",
        prompt="Fix deployment",
        repo_connected="octo/repo",
        status="completed",
        credits_charged=["5"],
        lines_generated={"count": 42},
        files_modified=True,
        nfet_phase_before="build",
        nfet_phase_after="validate",
        es_score_before=["0.1"],
        es_score_after={"value": 0.2},
        output_summary="Completed successfully",
        error_message=None,
        started_at="2026-01-04T03:04:05Z",
        completed_at=None,
    )

    response = user_routes._session_to_summary(session)

    assert response.credits_charged == 0
    assert response.lines_generated == 0
    assert response.files_modified == 0
    assert response.es_score_before is None
    assert response.es_score_after is None


def test_session_to_summary_tolerates_missing_legacy_fields() -> None:
    response = user_routes._session_to_summary(SimpleNamespace(id="session-2"))

    assert response.id == "session-2"
    assert response.mode == "unknown"
    assert response.prompt is None
    assert response.repo_connected is None
    assert response.status == "unknown"
    assert response.credits_charged == 0
    assert response.lines_generated == 0
    assert response.files_modified == 0
    assert response.nfet_phase_before is None
    assert response.nfet_phase_after is None
    assert response.es_score_before is None
    assert response.es_score_after is None
    assert response.output_summary is None
    assert response.error_message is None
    assert response.started_at == ""
    assert response.completed_at is None


def test_balance_to_response_defaults_malformed_payload_to_free_plan() -> None:
    response = user_routes._balance_to_response({"plan": ["pro"]})

    assert response.subscription_credits == 0
    assert response.topup_credits == 0
    assert response.total == 0
    assert response.used_this_month == 0
    assert response.plan == "free"
    assert response.monthly_allocation == user_routes.PLAN_CREDITS["free"]
