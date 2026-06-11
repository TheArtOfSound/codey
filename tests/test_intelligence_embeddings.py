from __future__ import annotations

import logging

import pytest

from codey.saas.intelligence import embeddings


def test_coerce_positive_rowcount_treats_unknown_counts_as_false() -> None:
    assert embeddings._coerce_positive_rowcount(None) is False
    assert embeddings._coerce_positive_rowcount(-1) is False
    assert embeddings._coerce_positive_rowcount(0) is False
    assert embeddings._coerce_positive_rowcount(True) is False
    assert embeddings._coerce_positive_rowcount(float("nan")) is False
    assert embeddings._coerce_positive_rowcount(float("inf")) is False
    assert embeddings._coerce_positive_rowcount("2") is True


def test_coerce_memory_search_row_skips_malformed_rows() -> None:
    parsed = embeddings._coerce_memory_search_row(
        ("mem-1", "context", "content", 0.9, 2, "0.81234")
    )

    assert parsed == (
        {
            "id": "mem-1",
            "memory_type": "context",
            "content": "content",
            "confidence": 0.9,
            "usage_count": 2,
            "similarity": 0.8123,
        },
        "mem-1",
    )
    assert embeddings._coerce_memory_search_row(("mem-1",)) is None
    assert embeddings._coerce_memory_search_row(
        ("mem-1", "context", "content", 0.9, 2, float("nan"))
    ) is None
    assert embeddings._coerce_memory_search_row(
        ("mem-1", "context", "content", 0.9, 2, 10**10000)
    ) is None
    assert embeddings._coerce_memory_search_row(
        (None, "context", "content", 0.9, 2, 0.8)
    ) is None


def test_embedding_row_list_coercion_rejects_malformed_results() -> None:
    row = ("mem-1", "context", "content", 0.9, 2, 0.8)

    assert embeddings._coerce_embedding_row_list([row]) == [row]
    assert embeddings._coerce_embedding_row_list((row,)) == [row]
    assert embeddings._coerce_embedding_row_list(None) == []
    assert embeddings._coerce_embedding_row_list("bad") == []


def test_normalize_dim_returns_fixed_finite_vectors_for_malformed_values() -> None:
    normalized = embeddings.EmbeddingService._normalize_dim(
        ["1.5", float("nan"), float("inf"), object()]
    )

    assert len(normalized) == embeddings.EMBEDDING_DIM
    assert normalized[:5] == [1.5, 0.0, 0.0, 0.0, 0.0]
    zero_vector = [0.0] * embeddings.EMBEDDING_DIM
    assert embeddings.EmbeddingService._normalize_dim("not-a-vector") == zero_vector


def test_normalize_dim_truncates_overlong_vectors() -> None:
    normalized = embeddings.EmbeddingService._normalize_dim(
        list(range(embeddings.EMBEDDING_DIM + 2))
    )

    assert len(normalized) == embeddings.EMBEDDING_DIM
    assert normalized[-1] == float(embeddings.EMBEDDING_DIM - 1)


@pytest.mark.asyncio
async def test_embedding_service_close_discards_closed_http_client() -> None:
    class _FakeHttpClient:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    svc = embeddings.EmbeddingService()
    client = _FakeHttpClient()
    svc._http = client

    await svc.close()

    assert client.closed is True
    assert svc._http is None


@pytest.mark.asyncio
async def test_embed_single_skips_http_when_embedding_keys_are_whitespace(
    monkeypatch,
) -> None:
    monkeypatch.setenv("COHERE_API_KEY", "   ")
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "   ")

    class _UnexpectedHttpClient:
        async def post(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(embeddings.httpx, "AsyncClient", lambda timeout: _UnexpectedHttpClient())

    svc = embeddings.EmbeddingService()
    try:
        result = await svc.embed_single("semantic search")
    finally:
        await svc.close()

    assert result is None


@pytest.mark.asyncio
async def test_embed_single_skips_http_when_embedding_keys_are_malformed(
    monkeypatch,
) -> None:
    monkeypatch.setenv("COHERE_API_KEY", "cohere bad")
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "hf\x7fbad")

    class _UnexpectedHttpClient:
        async def post(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(embeddings.httpx, "AsyncClient", lambda timeout: _UnexpectedHttpClient())

    svc = embeddings.EmbeddingService()
    try:
        result = await svc.embed_single("semantic search")
    finally:
        await svc.close()

    assert result is None


@pytest.mark.asyncio
async def test_embedding_provider_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("COHERE_API_KEY", "cohere-key")
    monkeypatch.delenv("HUGGINGFACE_API_KEY", raising=False)
    monkeypatch.setattr(embeddings, "_HTTPX_IMPORT_ERROR", None)

    class _FailingHttpClient:
        async def post(self, *args, **kwargs):
            raise RuntimeError(
                "provider failed "
                "https://user:secret@example.test/embed?api_key=svc-secret&client_secret=client123 "
                "access_token=abc123 auth_token=auth123 refresh_token=refresh123 "
                "password=pw123 for user@example.com authorization=Bearer bearer123"
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        embeddings.httpx,
        "AsyncClient",
        lambda timeout: _FailingHttpClient(),
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.embeddings")

    svc = embeddings.EmbeddingService()
    try:
        result = await svc.embed_single("semantic search")
    finally:
        await svc.close()

    assert result is None
    assert "user@example.com" not in caplog.text
    assert "user:secret" not in caplog.text
    assert "secret@example.test" not in caplog.text
    assert "svc-secret" not in caplog.text
    assert "client123" not in caplog.text
    assert "abc123" not in caplog.text
    assert "auth123" not in caplog.text
    assert "refresh123" not in caplog.text
    assert "pw123" not in caplog.text
    assert "bearer123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/embed?api_key=***&client_secret=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "auth_token=***" in caplog.text
    assert "refresh_token=***" in caplog.text
    assert "password=***" in caplog.text
    assert "authorization=Bearer ***" in caplog.text
    assert "Traceback" not in caplog.text
