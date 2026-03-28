"""Vector embedding service — Cohere + HuggingFace + pgvector.

Provides semantic memory retrieval for the fusion pipeline.
Embeddings are stored in the project_memories table (768-dim vectors)
and retrieved via cosine similarity search.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Embedding dimension must match project_memories.embedding vector(768)
EMBEDDING_DIM = 768


class EmbeddingService:
    """Generate embeddings via Cohere or HuggingFace, store/query in pgvector."""

    def __init__(self, *, timeout: float = 30) -> None:
        self._http = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._http.aclose()

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
        key = os.getenv("COHERE_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.post(
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
        except Exception:
            logger.debug("Cohere embedding failed", exc_info=True)
        return None

    async def _embed_huggingface(self, texts: list[str]) -> list[list[float]] | None:
        """Generate embeddings via HuggingFace Inference API (768-dim)."""
        key = os.getenv("HUGGINGFACE_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.post(
                "https://api-inference.huggingface.co/pipeline/feature-extraction/sentence-transformers/all-mpnet-base-v2",
                headers={"Authorization": f"Bearer {key}"},
                json={"inputs": texts[:32], "options": {"wait_for_model": True}},
            )
            if resp.status_code == 200:
                embeddings = resp.json()
                return [self._normalize_dim(e) for e in embeddings]
        except Exception:
            logger.debug("HuggingFace embedding failed", exc_info=True)
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
            text("""
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
            text("""
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
        for row in result.fetchall():
            memories.append({
                "id": str(row[0]),
                "memory_type": row[1],
                "content": row[2],
                "confidence": row[3],
                "usage_count": row[4],
                "similarity": round(float(row[5]), 4),
            })

            # Update usage count
            await db.execute(
                text("""
                    UPDATE project_memories
                    SET usage_count = usage_count + 1, last_used = now()
                    WHERE id = :id
                """),
                {"id": row[0]},
            )

        if memories:
            await db.commit()
        return memories

    async def delete_memory(self, db: AsyncSession, *, memory_id: str) -> bool:
        """Delete a specific memory by ID."""
        result = await db.execute(
            text("DELETE FROM project_memories WHERE id = :id"),
            {"id": memory_id},
        )
        await db.commit()
        return result.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_dim(embedding: list[float]) -> list[float]:
        """Pad or truncate embedding to EMBEDDING_DIM (768)."""
        if len(embedding) == EMBEDDING_DIM:
            return embedding
        if len(embedding) > EMBEDDING_DIM:
            return embedding[:EMBEDDING_DIM]
        # Pad with zeros
        return embedding + [0.0] * (EMBEDDING_DIM - len(embedding))


# Module-level singleton
embedding_service = EmbeddingService()
