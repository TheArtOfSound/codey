"""Vector embedding service — Cohere + HuggingFace + pgvector.

Provides semantic memory retrieval for the fusion pipeline.
Embeddings are stored in the project_memories table (768-dim vectors)
and retrieved via cosine similarity search.
"""
from __future__ import annotations

import logging
import math
import os
import re
from typing import Any

try:
    import httpx
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-light tests
    if exc.name != "httpx":
        raise
    _HTTPX_IMPORT_ERROR: ModuleNotFoundError | None = exc

    def _raise_missing_httpx(*args, **kwargs):
        raise RuntimeError("httpx is required for embedding provider calls") from _HTTPX_IMPORT_ERROR

    class _MissingHTTPX:
        AsyncClient = staticmethod(_raise_missing_httpx)

    httpx: Any = _MissingHTTPX()
else:  # pragma: no cover - depends on optional runtime dependency
    _HTTPX_IMPORT_ERROR = None

try:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-light tests
    if exc.name != "sqlalchemy":
        raise
    _SQLALCHEMY_IMPORT_ERROR: ModuleNotFoundError | None = exc
    text = None
    AsyncSession = Any
else:  # pragma: no cover - depends on optional runtime dependency
    _SQLALCHEMY_IMPORT_ERROR = None

logger = logging.getLogger(__name__)

# Embedding dimension must match project_memories.embedding vector(768)
EMBEDDING_DIM = 768
_URL_CREDENTIAL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|token|secret|password)=)[^&\s]+"
)
_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|token|secret|password|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


def _redact_embedding_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _NAMED_SECRET_RE.sub(r"\1***", text)
    return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)


def _require_httpx() -> None:
    if _HTTPX_IMPORT_ERROR is not None:
        raise RuntimeError("httpx is required for embedding provider calls") from _HTTPX_IMPORT_ERROR


def _require_sqlalchemy() -> None:
    if _SQLALCHEMY_IMPORT_ERROR is not None:
        raise RuntimeError("SQLAlchemy is required for embedding persistence") from _SQLALCHEMY_IMPORT_ERROR


def _sql_text(statement: str) -> Any:
    _require_sqlalchemy()
    assert text is not None
    return text(statement)


def _coerce_non_empty_embedding_secret(name: str) -> str | None:
    value = os.getenv(name)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        return None
    if any(char.isspace() for char in normalized):
        return None
    return normalized or None


def _coerce_positive_rowcount(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        rowcount = int(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return rowcount > 0


def _coerce_memory_similarity(value: object) -> float | None:
    try:
        similarity = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(similarity):
        return None
    return round(similarity, 4)


def _coerce_memory_search_row(row: object) -> tuple[dict[str, Any], object] | None:
    try:
        memory_id = row[0]  # type: ignore[index]
        memory_type = row[1]  # type: ignore[index]
        content = row[2]  # type: ignore[index]
        confidence = row[3]  # type: ignore[index]
        usage_count = row[4]  # type: ignore[index]
        similarity = _coerce_memory_similarity(row[5])  # type: ignore[index]
    except (TypeError, IndexError, KeyError):
        return None
    if memory_id is None or similarity is None:
        return None
    return (
        {
            "id": str(memory_id),
            "memory_type": memory_type,
            "content": content,
            "confidence": confidence,
            "usage_count": usage_count,
            "similarity": similarity,
        },
        memory_id,
    )


def _coerce_embedding_row_list(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


class EmbeddingService:
    """Generate embeddings via Cohere or HuggingFace, store/query in pgvector."""

    def __init__(self, *, timeout: float = 30) -> None:
        self._timeout = timeout
        self._http: Any | None = None

    def _http_client(self) -> Any:
        if self._http is None:
            _require_httpx()
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            http_client = self._http
            self._http = None
            await http_client.aclose()

    # ------------------------------------------------------------------
    # Embedding generation
    # ------------------------------------------------------------------

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Generate embeddings, trying Cohere first, then HuggingFace."""
        result = await self._embed_cohere(texts)
        if result is not None:
            return result
        result = await self._embed_huggingface(texts)
        return result

    async def embed_single(self, text: str) -> list[float] | None:
        """Embed a single text string."""
        result = await self.embed([text])
        if result and len(result) > 0:
            return result[0]
        return None

    async def _embed_cohere(self, texts: list[str]) -> list[list[float]] | None:
        """Generate embeddings via Cohere Embed v4 (768-dim)."""
        key = _coerce_non_empty_embedding_secret("COHERE_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http_client().post(
                "https://api.cohere.ai/v2/embed",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "texts": texts[:96],  # Cohere max batch size
                    "model": "embed-english-v3.0",
                    "input_type": "search_document",
                    "truncate": "END",
                },
            )
            if resp.status_code == 200:
                embeddings = resp.json().get("embeddings", [])
                # Cohere v3 returns 1024-dim; truncate/pad to 768
                return [self._normalize_dim(e) for e in embeddings]
        except Exception as exc:
            logger.debug(
                "Cohere embedding failed: %s", _redact_embedding_error(exc)
            )
        return None

    async def _embed_huggingface(self, texts: list[str]) -> list[list[float]] | None:
        """Generate embeddings via HuggingFace Inference API (768-dim)."""
        key = _coerce_non_empty_embedding_secret("HUGGINGFACE_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http_client().post(
                "https://api-inference.huggingface.co/pipeline/feature-extraction/sentence-transformers/all-mpnet-base-v2",
                headers={"Authorization": f"Bearer {key}"},
                json={"inputs": texts[:32], "options": {"wait_for_model": True}},
            )
            if resp.status_code == 200:
                embeddings = resp.json()
                return [self._normalize_dim(e) for e in embeddings]
        except Exception as exc:
            logger.debug(
                "HuggingFace embedding failed: %s", _redact_embedding_error(exc)
            )
        return None

    # ------------------------------------------------------------------
    # pgvector storage and retrieval
    # ------------------------------------------------------------------

    async def store_memory(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        content: str,
        memory_type: str = "context",
        project_id: str | None = None,
        confidence: float = 1.0,
    ) -> str | None:
        """Embed and store a memory in project_memories. Returns the memory ID."""
        embedding = await self.embed_single(content)
        if embedding is None:
            logger.warning("Could not generate embedding for memory storage")
            return None

        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        result = await db.execute(
            _sql_text("""
                INSERT INTO project_memories (user_id, project_id, memory_type, content, embedding, confidence)
                VALUES (:user_id, :project_id, :memory_type, :content, :embedding::vector, :confidence)
                RETURNING id
            """),
            {
                "user_id": user_id,
                "project_id": project_id,
                "memory_type": memory_type,
                "content": content,
                "embedding": embedding_str,
                "confidence": confidence,
            },
        )
        await db.commit()
        row = result.fetchone()
        return str(row[0]) if row else None

    async def search_memories(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        query: str,
        limit: int = 5,
        min_similarity: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Semantic search over a user's stored memories using cosine similarity."""
        embedding = await self.embed_single(query)
        if embedding is None:
            return []

        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        result = await db.execute(
            _sql_text("""
                SELECT
                    id, memory_type, content, confidence, usage_count,
                    1 - (embedding <=> :embedding::vector) AS similarity
                FROM project_memories
                WHERE user_id = :user_id
                    AND 1 - (embedding <=> :embedding::vector) > :min_similarity
                ORDER BY embedding <=> :embedding::vector
                LIMIT :limit
            """),
            {
                "user_id": user_id,
                "embedding": embedding_str,
                "min_similarity": min_similarity,
                "limit": limit,
            },
        )

        memories = []
        for row in _coerce_embedding_row_list(result.fetchall()):
            parsed_row = _coerce_memory_search_row(row)
            if parsed_row is None:
                logger.warning("Skipping malformed memory search row: %r", row)
                continue

            memory, memory_id = parsed_row
            memories.append(memory)

            # Update usage count
            await db.execute(
                _sql_text("""
                    UPDATE project_memories
                    SET usage_count = usage_count + 1, last_used = now()
                    WHERE id = :id
                """),
                {"id": memory_id},
            )

        if memories:
            await db.commit()
        return memories

    async def delete_memory(self, db: AsyncSession, *, memory_id: str) -> bool:
        """Delete a specific memory by ID."""
        result = await db.execute(
            _sql_text("DELETE FROM project_memories WHERE id = :id"),
            {"id": memory_id},
        )
        await db.commit()
        return _coerce_positive_rowcount(result.rowcount)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_dim(embedding: object) -> list[float]:
        """Pad or truncate embedding to EMBEDDING_DIM (768)."""
        if not isinstance(embedding, (list, tuple)):
            return [0.0] * EMBEDDING_DIM

        normalized: list[float] = []
        for value in embedding[:EMBEDDING_DIM]:
            try:
                parsed = float(value)
            except (TypeError, ValueError, OverflowError):
                parsed = 0.0
            if not math.isfinite(parsed):
                parsed = 0.0
            normalized.append(parsed)

        if len(normalized) < EMBEDDING_DIM:
            normalized.extend([0.0] * (EMBEDDING_DIM - len(normalized)))
        return normalized


# Module-level singleton
embedding_service = EmbeddingService()
