"""An in-memory vector store for retrieval-augmented generation (RAG).

This bundled store needs no external services and no embedding API. It uses a
deterministic, hashing-based bag-of-words embedding with cosine similarity. That
is enough to demonstrate end-to-end RAG offline and to power tests; for
production semantic search, implement :class:`~forge.memory.base.Memory` over a
real embedding model and vector database — the interface is identical, so agents
don't change.
"""

from __future__ import annotations

import math
import re

from forge.memory.base import Memory, MemoryItem

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class InMemoryVectorStore(Memory):
    """A simple cosine-similarity store over hashed bag-of-words vectors."""

    def __init__(self, *, dim: int = 512) -> None:
        self._dim = dim
        self._items: list[MemoryItem] = []
        self._vectors: list[list[float]] = []

    def _embed(self, text: str) -> list[float]:
        """Hash tokens into a fixed-dimension, L2-normalized term-frequency vector."""
        vector = [0.0] * self._dim
        for token in _tokenize(text):
            vector[hash(token) % self._dim] += 1.0
        norm = math.sqrt(sum(component * component for component in vector))
        if norm > 0:
            vector = [component / norm for component in vector]
        return vector

    async def add(self, text: str, *, metadata: dict[str, object] | None = None) -> str:
        item = MemoryItem(text=text, metadata=metadata or {})
        self._items.append(item)
        self._vectors.append(self._embed(text))
        return item.id

    async def search(self, query: str, *, k: int = 5) -> list[MemoryItem]:
        if not self._items:
            return []
        query_vector = self._embed(query)
        scored: list[MemoryItem] = []
        for item, vector in zip(self._items, self._vectors, strict=True):
            score = sum(a * b for a, b in zip(query_vector, vector, strict=True))
            scored.append(item.model_copy(update={"score": round(score, 6)}))
        scored.sort(key=lambda item: item.score or 0.0, reverse=True)
        return [item for item in scored[:k] if (item.score or 0.0) > 0.0]

    async def clear(self) -> None:
        self._items.clear()
        self._vectors.clear()

    def __len__(self) -> int:
        return len(self._items)
