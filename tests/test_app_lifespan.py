from __future__ import annotations

import importlib
import logging
import os
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest

import codey.saas.api.app as app_module


def test_load_secret_env_file_ignores_missing_or_unreadable_files(
    monkeypatch,
) -> None:
    monkeypatch.delenv("CODEY_TEST_SECRET", raising=False)

    class _MissingPath:
        def exists(self) -> bool:
            return False

        def read_text(self, *, encoding: str) -> str:
            assert encoding == "utf-8"
            raise AssertionError("missing files should not be read")

    class _UnreadablePath:
        def exists(self) -> bool:
            return True

        def read_text(self, *, encoding: str) -> str:
            assert encoding == "utf-8"
            raise OSError("permission denied")

    app_module._load_secret_env_file(_MissingPath())
    app_module._load_secret_env_file(_UnreadablePath())

    assert "CODEY_TEST_SECRET" not in os.environ


def test_load_secret_env_file_parses_key_value_lines(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("CODEY_TEST_SECRET", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "CODEY_TEST_SECRET = secret value \n"
        "MALFORMED\n",
        encoding="utf-8",
    )

    app_module._load_secret_env_file(env_file)

    assert os.environ["CODEY_TEST_SECRET"] == "secret value"


def test_load_secret_env_file_skips_invalid_keys(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CODEY_TEST_SECRET", raising=False)
    monkeypatch.delenv("BAD KEY", raising=False)
    monkeypatch.delenv("BAD-KEY", raising=False)
    monkeypatch.delenv("1BAD", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "=missing key\n"
        "BAD KEY=value\n"
        "BAD-KEY=value\n"
        "1BAD=value\n"
        "BAD\0KEY=value\n"
        "CODEY_TEST_SECRET=ok\n",
        encoding="utf-8",
    )

    app_module._load_secret_env_file(env_file)

    assert os.environ["CODEY_TEST_SECRET"] == "ok"
    assert "BAD KEY" not in os.environ
    assert "BAD-KEY" not in os.environ
    assert "1BAD" not in os.environ


def test_load_secret_env_file_ignores_oversized_files(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("CODEY_TEST_SECRET", raising=False)
    monkeypatch.setattr(app_module, "_MAX_SECRET_ENV_CHARS", 8)
    env_file = tmp_path / ".env"
    env_file.write_text("CODEY_TEST_SECRET=secret\n", encoding="utf-8")

    app_module._load_secret_env_file(env_file)

    assert "CODEY_TEST_SECRET" not in os.environ


def test_redact_connection_error_hides_common_secret_shapes() -> None:
    message = app_module._redact_connection_error(
        RuntimeError(
            "connect rediss://user:super-secret@example.com:6379/0"
            "?access_token=token-secret failed "
            "authorization=Bearer bearer-secret for operator@example.test"
        )
    )

    assert "super-secret" not in message
    assert "token-secret" not in message
    assert "bearer-secret" not in message
    assert "operator@example.test" not in message
    assert "rediss://***@example.com:6379/0" in message
    assert "access_token=***" in message
    assert "authorization=Bearer ***" in message
    assert "[redacted-email]" in message


@pytest.mark.asyncio
async def test_lifespan_cleanup_logs_redacted_failure(caplog) -> None:
    def cleanup() -> None:
        raise RuntimeError(
            "close rediss://user:super-secret@example.com:6379/0"
            "?client_secret=query-secret authorization=Bearer bearer-secret"
        )

    caplog.set_level(logging.WARNING, logger="codey")

    await app_module._run_lifespan_cleanup("cache", cleanup)

    assert "super-secret" not in caplog.text
    assert "query-secret" not in caplog.text
    assert "bearer-secret" not in caplog.text
    assert "rediss://***@example.com:6379/0" in caplog.text
    assert "client_secret=***" in caplog.text
    assert "authorization=Bearer ***" in caplog.text


@pytest.mark.asyncio
async def test_lifespan_closes_intelligence_resources(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_ensure_database_compatibility(_engine) -> None:
        calls.append("bootstrap")

    async def fake_close_cache() -> None:
        calls.append("cache")

    class _IntelligenceServices:
        async def close(self) -> None:
            calls.append("services")

    class _EmbeddingService:
        async def close(self) -> None:
            calls.append("embeddings")

    monkeypatch.setenv("CODEY_ENV", "development")
    monkeypatch.setenv("CODEY_BOOTSTRAP_STRIPE_ON_STARTUP", "false")
    monkeypatch.setattr(
        app_module,
        "ensure_database_compatibility",
        fake_ensure_database_compatibility,
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.cache",
        SimpleNamespace(close=fake_close_cache),
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.services",
        SimpleNamespace(intelligence_services=_IntelligenceServices()),
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.embeddings",
        SimpleNamespace(embedding_service=_EmbeddingService()),
    )

    async with app_module.lifespan(object()):
        assert calls == ["bootstrap"]

    assert calls[0] == "bootstrap"
    assert set(calls[1:]) == {"cache", "services", "embeddings"}


@pytest.mark.asyncio
async def test_lifespan_bootstraps_stripe_when_flag_has_whitespace(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_ensure_database_compatibility(_engine) -> None:
        calls.append("bootstrap")

    async def fake_setup_stripe_products() -> None:
        calls.append("stripe")

    async def fake_close_cache() -> None:
        calls.append("cache")

    class _IntelligenceServices:
        async def close(self) -> None:
            calls.append("services")

    class _EmbeddingService:
        async def close(self) -> None:
            calls.append("embeddings")

    monkeypatch.setenv("CODEY_ENV", "development")
    monkeypatch.setenv("CODEY_BOOTSTRAP_STRIPE_ON_STARTUP", " true ")
    monkeypatch.setattr(
        app_module,
        "ensure_database_compatibility",
        fake_ensure_database_compatibility,
    )
    monkeypatch.setattr(app_module, "setup_stripe_products", fake_setup_stripe_products)
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.cache",
        SimpleNamespace(close=fake_close_cache),
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.services",
        SimpleNamespace(intelligence_services=_IntelligenceServices()),
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.embeddings",
        SimpleNamespace(embedding_service=_EmbeddingService()),
    )

    async with app_module.lifespan(object()):
        assert calls == ["bootstrap", "stripe"]

    assert calls[:2] == ["bootstrap", "stripe"]
    assert set(calls[2:]) == {"cache", "services", "embeddings"}


@pytest.mark.asyncio
async def test_lifespan_redacts_stripe_bootstrap_failure(monkeypatch, caplog) -> None:
    calls: list[str] = []

    async def fake_ensure_database_compatibility(_engine) -> None:
        calls.append("bootstrap")

    async def fake_setup_stripe_products() -> None:
        raise RuntimeError(
            "stripe post https://api.stripe.com/v1/products"
            "?client_secret=query-secret authorization=Bearer stripe-secret "
            "for billing@example.test"
        )

    async def fake_close_cache() -> None:
        calls.append("cache")

    class _IntelligenceServices:
        async def close(self) -> None:
            calls.append("services")

    class _EmbeddingService:
        async def close(self) -> None:
            calls.append("embeddings")

    monkeypatch.setenv("CODEY_ENV", "development")
    monkeypatch.setenv("CODEY_BOOTSTRAP_STRIPE_ON_STARTUP", "true")
    monkeypatch.setattr(
        app_module,
        "ensure_database_compatibility",
        fake_ensure_database_compatibility,
    )
    monkeypatch.setattr(app_module, "setup_stripe_products", fake_setup_stripe_products)
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.cache",
        SimpleNamespace(close=fake_close_cache),
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.services",
        SimpleNamespace(intelligence_services=_IntelligenceServices()),
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.embeddings",
        SimpleNamespace(embedding_service=_EmbeddingService()),
    )
    caplog.set_level(logging.WARNING, logger="codey")

    async with app_module.lifespan(object()):
        assert calls == ["bootstrap"]

    assert "query-secret" not in caplog.text
    assert "stripe-secret" not in caplog.text
    assert "billing@example.test" not in caplog.text
    assert "client_secret=***" in caplog.text
    assert "authorization=Bearer ***" in caplog.text
    assert "[redacted-email]" in caplog.text
    assert set(calls[1:]) == {"cache", "services", "embeddings"}


@pytest.mark.asyncio
async def test_lifespan_cleanup_continues_after_close_failure(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_ensure_database_compatibility(_engine) -> None:
        return None

    async def fake_close_cache() -> None:
        calls.append("cache")
        raise RuntimeError("cache close failed")

    class _IntelligenceServices:
        async def close(self) -> None:
            calls.append("services")

    class _EmbeddingService:
        async def close(self) -> None:
            calls.append("embeddings")

    monkeypatch.setenv("CODEY_ENV", "development")
    monkeypatch.setenv("CODEY_BOOTSTRAP_STRIPE_ON_STARTUP", "false")
    monkeypatch.setattr(
        app_module,
        "ensure_database_compatibility",
        fake_ensure_database_compatibility,
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.cache",
        SimpleNamespace(close=fake_close_cache),
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.services",
        SimpleNamespace(intelligence_services=_IntelligenceServices()),
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.embeddings",
        SimpleNamespace(embedding_service=_EmbeddingService()),
    )

    async with app_module.lifespan(object()):
        pass

    assert set(calls) == {"cache", "services", "embeddings"}


@pytest.mark.asyncio
async def test_lifespan_cleanup_continues_after_sync_close_failure(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_ensure_database_compatibility(_engine) -> None:
        return None

    def fake_close_cache() -> None:
        calls.append("cache")
        raise RuntimeError("cache close failed before coroutine creation")

    class _IntelligenceServices:
        async def close(self) -> None:
            calls.append("services")

    class _EmbeddingService:
        async def close(self) -> None:
            calls.append("embeddings")

    monkeypatch.setenv("CODEY_ENV", "development")
    monkeypatch.setenv("CODEY_BOOTSTRAP_STRIPE_ON_STARTUP", "false")
    monkeypatch.setattr(
        app_module,
        "ensure_database_compatibility",
        fake_ensure_database_compatibility,
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.cache",
        SimpleNamespace(close=fake_close_cache),
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.services",
        SimpleNamespace(intelligence_services=_IntelligenceServices()),
    )
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.embeddings",
        SimpleNamespace(embedding_service=_EmbeddingService()),
    )

    async with app_module.lifespan(object()):
        pass

    assert set(calls) == {"cache", "services", "embeddings"}


@pytest.mark.asyncio
async def test_lifespan_rejects_whitespace_padded_production_env(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_ensure_database_compatibility(_engine) -> None:
        calls.append("bootstrap")

    monkeypatch.setenv("CODEY_ENV", " production ")
    monkeypatch.setenv("CODEY_BOOTSTRAP_STRIPE_ON_STARTUP", "false")
    monkeypatch.setattr(
        app_module,
        "ensure_database_compatibility",
        fake_ensure_database_compatibility,
    )
    monkeypatch.setattr(app_module.settings, "secret_key", "change-me-in-production")

    with pytest.raises(RuntimeError, match="SECRET_KEY must be set"):
        async with app_module.lifespan(object()):
            pass

    assert calls == []


@pytest.mark.asyncio
async def test_lifespan_rejects_whitespace_padded_default_secret_key(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_ensure_database_compatibility(_engine) -> None:
        calls.append("bootstrap")

    monkeypatch.setenv("CODEY_ENV", "production")
    monkeypatch.setenv("CODEY_BOOTSTRAP_STRIPE_ON_STARTUP", "false")
    monkeypatch.setattr(
        app_module,
        "ensure_database_compatibility",
        fake_ensure_database_compatibility,
    )
    monkeypatch.setattr(app_module.settings, "secret_key", " change-me-in-production ")

    with pytest.raises(RuntimeError, match="SECRET_KEY must be set"):
        async with app_module.lifespan(object()):
            pass

    assert calls == []


@pytest.mark.asyncio
async def test_lifespan_rejects_blank_secret_key_in_production(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_ensure_database_compatibility(_engine) -> None:
        calls.append("bootstrap")

    monkeypatch.setenv("CODEY_ENV", "production")
    monkeypatch.setenv("CODEY_BOOTSTRAP_STRIPE_ON_STARTUP", "false")
    monkeypatch.setattr(
        app_module,
        "ensure_database_compatibility",
        fake_ensure_database_compatibility,
    )
    monkeypatch.setattr(app_module.settings, "secret_key", "   ")

    with pytest.raises(RuntimeError, match="SECRET_KEY must be set"):
        async with app_module.lifespan(object()):
            pass

    assert calls == []


def test_app_import_tolerates_invalid_sentry_sample_rate(monkeypatch) -> None:
    init_calls: list[dict[str, object]] = []

    class _OverflowingSampleRate:
        def __str__(self) -> str:
            raise OverflowError("sample rate too large")

    def fake_init(**kwargs) -> None:
        init_calls.append(kwargs)

    class _FastApiIntegration:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _StarletteIntegration:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    sentry_sdk_module = ModuleType("sentry_sdk")
    sentry_sdk_module.init = fake_init
    sentry_integrations_module = ModuleType("sentry_sdk.integrations")
    sentry_fastapi_module = ModuleType("sentry_sdk.integrations.fastapi")
    sentry_fastapi_module.FastApiIntegration = _FastApiIntegration
    sentry_starlette_module = ModuleType("sentry_sdk.integrations.starlette")
    sentry_starlette_module.StarletteIntegration = _StarletteIntegration

    monkeypatch.setenv("SENTRY_DSN", "https://example.invalid/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "nan")
    monkeypatch.setitem(sys.modules, "sentry_sdk", sentry_sdk_module)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", sentry_integrations_module)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.fastapi", sentry_fastapi_module)
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk.integrations.starlette",
        sentry_starlette_module,
    )

    reloaded = importlib.reload(app_module)

    assert reloaded._coerce_sentry_traces_sample_rate("not-a-number") == 0.1
    assert reloaded._coerce_sentry_traces_sample_rate("nan") == 0.1
    assert reloaded._coerce_sentry_traces_sample_rate("inf") == 0.1
    assert reloaded._coerce_sentry_traces_sample_rate("-inf") == 0.1
    assert reloaded._coerce_sentry_traces_sample_rate(_OverflowingSampleRate()) == 0.1
    assert init_calls[-1]["traces_sample_rate"] == 0.1


def test_app_import_ignores_whitespace_sentry_dsn(monkeypatch) -> None:
    init_calls: list[dict[str, object]] = []

    def fake_init(**kwargs) -> None:
        init_calls.append(kwargs)

    class _FastApiIntegration:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _StarletteIntegration:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    sentry_sdk_module = ModuleType("sentry_sdk")
    sentry_sdk_module.init = fake_init
    sentry_integrations_module = ModuleType("sentry_sdk.integrations")
    sentry_fastapi_module = ModuleType("sentry_sdk.integrations.fastapi")
    sentry_fastapi_module.FastApiIntegration = _FastApiIntegration
    sentry_starlette_module = ModuleType("sentry_sdk.integrations.starlette")
    sentry_starlette_module.StarletteIntegration = _StarletteIntegration

    monkeypatch.setenv("SENTRY_DSN", "   ")
    monkeypatch.setitem(sys.modules, "sentry_sdk", sentry_sdk_module)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", sentry_integrations_module)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.fastapi", sentry_fastapi_module)
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk.integrations.starlette",
        sentry_starlette_module,
    )

    reloaded = importlib.reload(app_module)

    assert reloaded._coerce_non_empty_env_text("   ") is None
    assert reloaded._coerce_non_empty_env_text("prod\tuction") is None
    assert init_calls == []


def test_app_import_ignores_malformed_sentry_dsn(monkeypatch) -> None:
    init_calls: list[dict[str, object]] = []

    def fake_init(**kwargs) -> None:
        init_calls.append(kwargs)

    class _FastApiIntegration:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _StarletteIntegration:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    sentry_sdk_module = ModuleType("sentry_sdk")
    sentry_sdk_module.init = fake_init
    sentry_integrations_module = ModuleType("sentry_sdk.integrations")
    sentry_fastapi_module = ModuleType("sentry_sdk.integrations.fastapi")
    sentry_fastapi_module.FastApiIntegration = _FastApiIntegration
    sentry_starlette_module = ModuleType("sentry_sdk.integrations.starlette")
    sentry_starlette_module.StarletteIntegration = _StarletteIntegration

    monkeypatch.setenv("SENTRY_DSN", "https://example.invalid:bad/1")
    monkeypatch.setitem(sys.modules, "sentry_sdk", sentry_sdk_module)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", sentry_integrations_module)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.fastapi", sentry_fastapi_module)
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk.integrations.starlette",
        sentry_starlette_module,
    )

    reloaded = importlib.reload(app_module)

    assert reloaded._coerce_sentry_dsn("https://public@example.invalid/1") == (
        "https://public@example.invalid/1"
    )
    assert reloaded._coerce_sentry_dsn("https://example.invalid:bad/1") is None
    assert reloaded._coerce_sentry_dsn("https://example.invalid/1#fragment") is None
    assert reloaded._coerce_sentry_dsn("https://public:secret@example.invalid/1") is None
    assert reloaded._coerce_sentry_dsn("https://example invalid/1") is None
    assert init_calls == []


def test_app_import_normalizes_whitespace_sentry_environment(monkeypatch) -> None:
    init_calls: list[dict[str, object]] = []

    def fake_init(**kwargs) -> None:
        init_calls.append(kwargs)

    class _FastApiIntegration:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _StarletteIntegration:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    sentry_sdk_module = ModuleType("sentry_sdk")
    sentry_sdk_module.init = fake_init
    sentry_integrations_module = ModuleType("sentry_sdk.integrations")
    sentry_fastapi_module = ModuleType("sentry_sdk.integrations.fastapi")
    sentry_fastapi_module.FastApiIntegration = _FastApiIntegration
    sentry_starlette_module = ModuleType("sentry_sdk.integrations.starlette")
    sentry_starlette_module.StarletteIntegration = _StarletteIntegration

    monkeypatch.setenv("SENTRY_DSN", "https://example.invalid/1")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "   ")
    monkeypatch.setitem(sys.modules, "sentry_sdk", sentry_sdk_module)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", sentry_integrations_module)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.fastapi", sentry_fastapi_module)
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk.integrations.starlette",
        sentry_starlette_module,
    )

    reloaded = importlib.reload(app_module)

    assert reloaded._coerce_non_empty_env_text("   ") is None
    assert init_calls[-1]["environment"] == "production"


def test_cors_allowed_origins_rejects_whitespace_settings_and_extras(monkeypatch) -> None:
    monkeypatch.setattr(app_module.settings, "frontend_url", "   ")
    monkeypatch.setattr(app_module.settings, "api_url", "   ")
    monkeypatch.setenv("CODEY_CORS_ORIGINS", " https://docs.example.com ,   ,")

    origins = app_module._cors_allowed_origins()

    assert "   " not in origins
    assert "https://docs.example.com" in origins
    assert "http://localhost:3000" in origins


def test_cors_allowed_origins_normalizes_paths_and_rejects_malformed_values(monkeypatch) -> None:
    monkeypatch.setattr(app_module.settings, "frontend_url", " https://app.example.com/dashboard ")
    monkeypatch.setattr(app_module.settings, "api_url", "   ")
    monkeypatch.setenv(
        "CODEY_CORS_ORIGINS",
        " https://docs.example.com/guide , app.example.com ,",
    )

    origins = app_module._cors_allowed_origins()

    assert "https://app.example.com" in origins
    assert "https://app.example.com/dashboard" not in origins
    assert "https://docs.example.com" in origins
    assert "app.example.com" not in origins


def test_cors_allowed_origins_rejects_credentialed_origins(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module.settings,
        "frontend_url",
        "https://user:pass@app.example.com",
    )
    monkeypatch.setattr(app_module.settings, "api_url", "   ")
    monkeypatch.setenv(
        "CODEY_CORS_ORIGINS",
        "https://docs:secret@example.com,https://docs.example.com",
    )

    origins = app_module._cors_allowed_origins()

    assert "https://user:pass@app.example.com" not in origins
    assert "https://docs:secret@example.com" not in origins
    assert "https://docs.example.com" in origins


def test_cors_allowed_origins_rejects_invalid_ports(monkeypatch) -> None:
    monkeypatch.setattr(app_module.settings, "frontend_url", "https://app.example.com:bad")
    monkeypatch.setattr(app_module.settings, "api_url", "   ")
    monkeypatch.setenv(
        "CODEY_CORS_ORIGINS",
        "https://docs.example.com:bad,https://zero.example.com:0,https://docs.example.com",
    )

    origins = app_module._cors_allowed_origins()

    assert "https://app.example.com:bad" not in origins
    assert "https://docs.example.com:bad" not in origins
    assert "https://zero.example.com:0" not in origins
    assert "https://docs.example.com" in origins


def test_cors_allowed_origins_rejects_control_character_origins(monkeypatch) -> None:
    monkeypatch.setattr(app_module.settings, "frontend_url", "https://app.example.com\n.evil")
    monkeypatch.setattr(app_module.settings, "api_url", "https://api.example.com\t.evil")
    monkeypatch.setenv(
        "CODEY_CORS_ORIGINS",
        "https://docs.example.com\t.evil,https://docs.example.com",
    )

    origins = app_module._cors_allowed_origins()

    assert "https://app.example.com.evil" not in origins
    assert "https://api.example.com.evil" not in origins
    assert "https://docs.example.com.evil" not in origins
    assert "https://docs.example.com" in origins


def test_cors_allowed_origins_rejects_internal_whitespace_origins(monkeypatch) -> None:
    monkeypatch.setattr(app_module.settings, "frontend_url", "https://app example.com")
    monkeypatch.setattr(app_module.settings, "api_url", "https://api.example.com bad")
    monkeypatch.setenv(
        "CODEY_CORS_ORIGINS",
        "https://docs example.com,https://docs.example.com",
    )

    origins = app_module._cors_allowed_origins()

    assert "https://app example.com" not in origins
    assert "https://api.example.com bad" not in origins
    assert "https://docs example.com" not in origins
    assert "https://docs.example.com" in origins
