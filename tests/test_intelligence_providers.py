from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import codey.saas.intelligence.providers as providers


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


@pytest.mark.asyncio
async def test_call_model_once_normalizes_mapping_message_content(monkeypatch) -> None:
    async def fake_create(**kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content={"content": "generated output"})
                )
            ]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_create)
        )
    )
    monkeypatch.setattr(providers, "get_client", lambda _provider: fake_client)

    result = await providers._call_model_once(
        "stub",
        "stub-model",
        [{"role": "user", "content": "prompt"}],
    )

    assert result == "generated output"


@pytest.mark.asyncio
async def test_call_model_once_stream_normalizes_structured_delta_content(
    monkeypatch,
) -> None:
    async def fake_create(**kwargs):
        assert kwargs["stream"] is True
        return _FakeStream(
            [
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content={"text": "hello"}))]
                ),
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content={"text": " world"}))]
                ),
            ]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_create)
        )
    )
    monkeypatch.setattr(providers, "get_client", lambda _provider: fake_client)

    result = await providers._call_model_once(
        "stub",
        "stub-model",
        [{"role": "user", "content": "prompt"}],
        stream=True,
    )

    assert result == "hello world"


@pytest.mark.asyncio
async def test_call_model_once_stream_skips_malformed_chunks(monkeypatch) -> None:
    async def fake_create(**kwargs):
        assert kwargs["stream"] is True
        return _FakeStream(
            [
                SimpleNamespace(choices=None),
                SimpleNamespace(choices=[]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace())]),
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))]
                ),
            ]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    monkeypatch.setattr(providers, "get_client", lambda _provider: fake_client)

    result = await providers._call_model_once(
        "stub",
        "stub-model",
        [{"role": "user", "content": "prompt"}],
        stream=True,
    )

    assert result == "ok"


@pytest.mark.asyncio
async def test_call_model_once_rejects_empty_non_stream_choices(monkeypatch) -> None:
    async def fake_create(**kwargs):
        return SimpleNamespace(choices=[])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    monkeypatch.setattr(providers, "get_client", lambda _provider: fake_client)

    with pytest.raises(RuntimeError, match="no choices"):
        await providers._call_model_once(
            "stub",
            "stub-model",
            [{"role": "user", "content": "prompt"}],
        )


def test_get_available_providers_ignores_whitespace_only_api_keys(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "   ")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    available = providers.get_available_providers()

    assert "groq" not in available


def test_get_available_providers_ignores_control_character_api_keys(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", " groq\tkey ")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    available = providers.get_available_providers()

    assert "groq" not in available


def test_get_available_providers_ignores_internal_whitespace_api_keys(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", " groq key ")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    available = providers.get_available_providers()

    assert "groq" not in available


def test_get_client_rejects_whitespace_only_api_key(monkeypatch) -> None:
    monkeypatch.setattr(providers, "_client_cache", {})
    monkeypatch.setenv("GROQ_API_KEY", "   ")

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        providers.get_client("groq")


def test_get_client_rejects_control_character_api_key(monkeypatch) -> None:
    monkeypatch.setattr(providers, "_client_cache", {})
    monkeypatch.setenv("GROQ_API_KEY", "groq\nkey")

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        providers.get_client("groq")


def test_get_client_rejects_internal_whitespace_api_key(monkeypatch) -> None:
    monkeypatch.setattr(providers, "_client_cache", {})
    monkeypatch.setenv("GROQ_API_KEY", "groq key")

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        providers.get_client("groq")


def test_resolve_model_falls_back_when_preferred_provider_key_is_whitespace(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "   ")
    monkeypatch.setenv("GROQ_API_KEY", " groq-test-key ")

    provider, model = providers.resolve_model("architecture")

    assert provider == "groq"
    assert model == providers.MODELS["default"]["model"]


def test_resolve_model_falls_back_when_preferred_provider_key_has_control_char(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter\177key")
    monkeypatch.setenv("GROQ_API_KEY", " groq-test-key ")

    provider, model = providers.resolve_model("architecture")

    assert provider == "groq"
    assert model == providers.MODELS["default"]["model"]


def test_resolve_model_falls_back_when_preferred_provider_key_has_whitespace(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter key")
    monkeypatch.setenv("GROQ_API_KEY", " groq-test-key ")

    provider, model = providers.resolve_model("architecture")

    assert provider == "groq"
    assert model == providers.MODELS["default"]["model"]


@pytest.mark.asyncio
async def test_call_model_fallback_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_call_model_once(provider, model, messages, **kwargs):
        calls.append((provider, model))
        if len(calls) == 1:
            raise RuntimeError(
                "429 rate limit "
                "https://user:secret@example.test/primary?token=primary-token "
                "for user@example.com"
            )
        raise RuntimeError(
            "fallback failed "
            "https://user:secret@example.test/fallback?access_token=fallback-token&client_secret=query-client "
            "auth_token=auth-token refresh_token=refresh-token password=pw-token "
            "authorization=Bearer abc123 for user@example.com"
        )

    monkeypatch.setattr(providers, "_call_model_once", fake_call_model_once)
    monkeypatch.setattr(
        providers,
        "FALLBACK_MODELS",
        [
            {
                "provider": "fb/https://user:secret@example.test/provider?token=provider-token",
                "model": "model/https://user:secret@example.test/model?token=model-token",
            }
        ],
    )
    caplog.set_level(logging.WARNING, logger="codey.saas.intelligence.providers")

    with pytest.raises(RuntimeError, match="429 rate limit"):
        await providers.call_model(
            "primary/https://user:secret@example.test/provider?token=primary-provider-token",
            "model/https://user:secret@example.test/model?token=primary-model-token",
            [{"role": "user", "content": "hello"}],
        )

    assert len(calls) == 2
    assert "user@example.com" not in caplog.text
    assert "user:secret" not in caplog.text
    assert "secret@example.test" not in caplog.text
    assert "primary-provider-token" not in caplog.text
    assert "primary-model-token" not in caplog.text
    assert "provider-token" not in caplog.text
    assert "model-token" not in caplog.text
    assert "fallback-token" not in caplog.text
    assert "query-client" not in caplog.text
    assert "auth-token" not in caplog.text
    assert "refresh-token" not in caplog.text
    assert "pw-token" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/provider?token=***" in caplog.text
    assert "https://***@example.test/model?token=***" in caplog.text
    assert "https://***@example.test/fallback?access_token=***&client_secret=***" in caplog.text
    assert "auth_token=***" in caplog.text
    assert "refresh_token=***" in caplog.text
    assert "password=***" in caplog.text
    assert "authorization=Bearer ***" in caplog.text
    assert "Traceback" not in caplog.text
