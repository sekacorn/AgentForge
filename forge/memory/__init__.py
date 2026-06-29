"""Memory: short-term conversation buffers and long-term retrieval (RAG)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge.memory.base import Memory, MemoryItem
from forge.memory.conversation import ConversationMemory
from forge.memory.sqlite import SQLiteMemoryStore
from forge.memory.vector import InMemoryVectorStore

if TYPE_CHECKING:
    from forge.config import ForgeConfig

__all__ = [
    "Memory",
    "MemoryItem",
    "ConversationMemory",
    "InMemoryVectorStore",
    "SQLiteMemoryStore",
    "build_memory",
]


def build_memory(config: ForgeConfig) -> Memory:
    """Construct the retrieval-memory backend named by ``config``.

    Defaults to the dependency-free :class:`InMemoryVectorStore`. When
    ``config.memory_backend == "sqlite"`` a :class:`SQLiteMemoryStore` is returned
    instead (it needs the optional ``aiosqlite`` dependency, loaded on first use,
    which raises a clear error if missing).
    """
    if config.memory_backend == "sqlite":
        return SQLiteMemoryStore(config.memory_path)
    return InMemoryVectorStore()
