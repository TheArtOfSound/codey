from __future__ import annotations

from codey.saas.billing.plans import PLANS
from codey.saas.models.user import User


def test_user_total_credits_tolerates_legacy_values() -> None:
    user = User(credits_remaining=None, topup_credits="7")

    assert user.total_credits == 7


def test_user_total_credits_fails_closed_for_invalid_values() -> None:
    user = User(credits_remaining="bad", topup_credits=None)

    assert user.total_credits == 0


def test_user_plan_display_name_tolerates_missing_plan() -> None:
    user = User(plan=None)

    assert user.plan_display_name == "Free"


def test_user_plan_display_name_normalizes_unknown_plans() -> None:
    user = User(plan="  custom_enterprise  ")

    assert user.plan_display_name == "Custom_enterprise"


def test_user_plan_display_name_uses_billing_plan_config() -> None:
    user = User(plan="starter")

    assert user.plan_display_name == "Starter"


def test_user_is_pro_or_above_uses_billing_plan_features() -> None:
    assert User(plan="starter").is_pro_or_above is False
    assert User(plan="  Pro ").is_pro_or_above is True
    assert User(plan="team").is_pro_or_above is True


def test_user_is_pro_or_above_preserves_legacy_enterprise_and_fails_closed() -> None:
    assert User(plan="enterprise").is_pro_or_above is True
    assert User(plan=None).is_pro_or_above is False
    assert User(plan={"name": "pro"}).is_pro_or_above is False
    assert User(plan="custom").is_pro_or_above is False


def test_user_is_pro_or_above_coerces_malformed_feature_flags(monkeypatch) -> None:
    monkeypatch.setitem(
        PLANS,
        "custom_pro",
        {
            "name": "Custom Pro",
            "features": {
                "autonomous_mode": "false",
            },
        },
    )

    assert User(plan="custom_pro").is_pro_or_above is False


def test_user_feature_bool_rejects_non_finite_values() -> None:
    assert User._coerce_feature_bool(float("nan"), fallback=False) is False
    assert User._coerce_feature_bool(float("inf"), fallback=False) is False
    assert User._coerce_feature_bool(1, fallback=False) is True
    assert User._coerce_feature_bool("yes", fallback=False) is True
