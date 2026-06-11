from __future__ import annotations

import logging
import sys
import types
from unittest.mock import AsyncMock

import pytest

import codey.saas.intelligence.ensemble as ensemble_module
from codey.saas.intelligence.ensemble import AssessmentResult, ExecutionResult, ModelEnsemble
from codey.saas.intelligence.router import TaskConfig


@pytest.mark.asyncio
async def test_call_and_measure_tolerates_structured_message_content(monkeypatch) -> None:
    ensemble = ModelEnsemble()

    async def fake_call_model(provider, model, messages, **kwargs) -> str:
        assert provider == "stub"
        assert model == "stub"
        return "generated output"

    monkeypatch.setattr(ensemble_module, "call_model", fake_call_model)

    result = await ensemble._call_and_measure(
        "stub",
        "stub",
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello world"},
                    {"type": "input_text", "text": "extra context"},
                    {"type": "image_url", "image_url": "https://example.invalid/image.png"},
                ],
            }
        ],
        TaskConfig(primary="default"),
    )

    assert result.content == "generated output"
    assert result.tokens_in > 0
    assert result.tokens_out > 0


@pytest.mark.asyncio
async def test_call_and_measure_normalizes_mapping_model_output(monkeypatch) -> None:
    ensemble = ModelEnsemble()

    async def fake_call_model(provider, model, messages, **kwargs):
        assert provider == "stub"
        assert model == "stub"
        return {"content": "generated output"}

    monkeypatch.setattr(ensemble_module, "call_model", fake_call_model)

    result = await ensemble._call_and_measure(
        "stub",
        "stub",
        [{"role": "user", "content": "prompt"}],
        TaskConfig(primary="default"),
    )

    assert result.content == "generated output"
    assert result.tokens_out > 0


@pytest.mark.asyncio
async def test_execute_single_treats_false_string_disable_auto_fix_as_enabled(
    monkeypatch,
) -> None:
    ensemble = ModelEnsemble()

    monkeypatch.setattr(
        ensemble,
        "_inject_memory_context",
        AsyncMock(return_value=[{"role": "user", "content": "prompt"}]),
    )
    monkeypatch.setattr(
        ensemble_module,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )
    monkeypatch.setattr(
        ensemble,
        "_call_and_measure",
        AsyncMock(
            return_value=ExecutionResult(
                content="def broken():\n    return 1\n",
                model_used="stub",
                provider_used="stub",
                tokens_in=1,
                tokens_out=1,
                latency_ms=1.0,
            )
        ),
    )
    monkeypatch.setattr(ensemble, "_looks_like_code", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        ensemble,
        "assess_output",
        AsyncMock(
            side_effect=[
                AssessmentResult(score=0.1, issues=[], passed=False),
                AssessmentResult(score=0.9, issues=[], passed=True),
            ]
        ),
    )
    auto_fix = AsyncMock(return_value="def fixed():\n    return 2\n")
    monkeypatch.setattr(ensemble, "auto_fix", auto_fix)

    result = await ensemble._execute_single(
        TaskConfig(primary="default"),
        [{"role": "user", "content": "prompt"}],
        {"disable_auto_fix": "false"},
    )

    auto_fix.assert_awaited_once()
    assert result.content == "def fixed():\n    return 2\n"


@pytest.mark.asyncio
async def test_auto_fix_accepts_mapping_model_output(monkeypatch) -> None:
    ensemble = ModelEnsemble()
    issues = [ensemble_module.Issue(severity="error", message="broken", line=1)]

    monkeypatch.setattr(
        ensemble_module,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )

    async def fake_call_model(provider, model, messages, **kwargs):
        assert provider == "stub"
        assert model == "stub"
        return {"content": "```python\nprint('fixed')\n```"}

    monkeypatch.setattr(ensemble_module, "call_model", fake_call_model)
    monkeypatch.setattr(
        ensemble,
        "assess_output",
        AsyncMock(return_value=AssessmentResult(score=1.0, issues=[], passed=True)),
    )

    fixed = await ensemble.auto_fix("print('broken'", issues)

    assert fixed == "print('fixed')\n"


@pytest.mark.asyncio
async def test_auto_fix_redacts_failed_attempt_logs(monkeypatch, caplog) -> None:
    ensemble = ModelEnsemble()
    issues = [ensemble_module.Issue(severity="error", message="broken", line=1)]

    monkeypatch.setattr(
        ensemble_module,
        "resolve_model",
        lambda *_args, **_kwargs: ("stub", "stub"),
    )

    async def fail_call_model(provider, model, messages, **kwargs):
        raise RuntimeError(
            "provider failed https://user:secret@example.test/model?api_key=sk-secret"
            " authorization: Bearer ensemble-auth"
        )

    monkeypatch.setattr(ensemble_module, "call_model", fail_call_model)
    caplog.set_level(logging.WARNING, logger="codey.saas.intelligence.ensemble")

    fixed = await ensemble.auto_fix("print('broken')", issues)

    assert fixed == "print('broken')"
    assert "secret" not in caplog.text.lower()
    assert "sk-secret" not in caplog.text
    assert "ensemble-auth" not in caplog.text
    assert "https://***@example.test/model?api_key=***" in caplog.text
    assert "authorization: Bearer ***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_assess_output_redacts_semgrep_integration_failures(
    monkeypatch,
    caplog,
) -> None:
    async def fail_semgrep_scan(*args, **kwargs):
        raise RuntimeError(
            "semgrep failed https://user:secret@example.test/scan?api_key=sk-secret&client_secret=client123 "
            "access_token=abc123 auth_token=auth123 refresh_token=refresh123 "
            "password=pw123 authorization=Bearer bearer123 for user@example.com"
        )

    monkeypatch.setattr(
        ensemble_module.intelligence_services,
        "semgrep_scan",
        fail_semgrep_scan,
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.ensemble")

    result = await ModelEnsemble().assess_output("print('ok')", {"language": "python"})

    assert result.passed is True
    assert "user@example.com" not in caplog.text
    assert "user:secret" not in caplog.text
    assert "secret@example.test" not in caplog.text
    assert "sk-secret" not in caplog.text
    assert "client123" not in caplog.text
    assert "abc123" not in caplog.text
    assert "auth123" not in caplog.text
    assert "refresh123" not in caplog.text
    assert "pw123" not in caplog.text
    assert "bearer123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/scan?api_key=***&client_secret=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "auth_token=***" in caplog.text
    assert "refresh_token=***" in caplog.text
    assert "password=***" in caplog.text
    assert "authorization=Bearer ***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_inject_memory_context_redacts_lookup_failures(
    monkeypatch,
    caplog,
) -> None:
    class _FailingEmbeddingService:
        async def search_memories(self, *args, **kwargs):
            raise RuntimeError(
                "memory failed https://user:secret@example.test/memory?token=mem-token&client_secret=client123 "
                "auth_token=auth123 refresh_token=refresh123 password=pw123 "
                "authorization=abc123 for user@example.com"
            )

    fake_embeddings = types.ModuleType("codey.saas.intelligence.embeddings")
    fake_embeddings.embedding_service = _FailingEmbeddingService()
    monkeypatch.setitem(
        sys.modules,
        "codey.saas.intelligence.embeddings",
        fake_embeddings,
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.ensemble")

    messages = [{"role": "user", "content": "hello"}]
    result = await ModelEnsemble()._inject_memory_context(
        messages,
        {"user_id": "user-1", "db": object()},
    )

    assert result == messages
    assert "user@example.com" not in caplog.text
    assert "user:secret" not in caplog.text
    assert "secret@example.test" not in caplog.text
    assert "mem-token" not in caplog.text
    assert "client123" not in caplog.text
    assert "auth123" not in caplog.text
    assert "refresh123" not in caplog.text
    assert "pw123" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/memory?token=***&client_secret=***" in caplog.text
    assert "auth_token=***" in caplog.text
    assert "refresh_token=***" in caplog.text
    assert "password=***" in caplog.text
    assert "authorization=***" in caplog.text
    assert "Traceback" not in caplog.text
