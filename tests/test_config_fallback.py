from __future__ import annotations

import builtins
import sys


def _import_config_without_pydantic_settings(monkeypatch):
    sys.modules.pop("codey.saas.config", None)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pydantic_settings":
            raise ModuleNotFoundError(
                "No module named 'pydantic_settings'",
                name="pydantic_settings",
            )
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    import codey.saas.config as config

    return config


def test_config_import_falls_back_without_pydantic_settings(monkeypatch) -> None:
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "42")

    config = _import_config_without_pydantic_settings(monkeypatch)

    assert config.settings.jwt_expire_minutes == 42
    assert config.settings.secret_key == "change-me-in-production"
    assert config.settings.redis_url == "redis://localhost:6379"


def test_config_fallback_rejects_malformed_int_settings(monkeypatch) -> None:
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "inf")

    config = _import_config_without_pydantic_settings(monkeypatch)

    assert config.settings.jwt_expire_minutes == 60 * 24 * 7
    assert config.Settings(jwt_expire_minutes=True).jwt_expire_minutes == 60 * 24 * 7
    assert (
        config.Settings(jwt_expire_minutes=float("inf")).jwt_expire_minutes
        == 60 * 24 * 7
    )


def test_settings_redis_url_normalization_falls_back_for_invalid_values() -> None:
    import codey.saas.config as config

    assert (
        config._normalize_settings_redis_url("redis://localhost:not-a-port/0")
        == "redis://localhost:6379"
    )
