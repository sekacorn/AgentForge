"""Memory abstractions.

Forge distinguishes two kinds of memory:

* **Conversation memory** (short-term) — the running transcript an agent reasons
  over. See :class:`~forge.memory.conversation.ConversationMemory`.
* **Retrieval memory** (long-term / RAG) — a searchable store of documents the
  agent can pull relevant context from. Implementations satisfy
  :class:`Memory` below.

The :class:`Memory` interface is intentionally tiny (``add`` / ``search`` /
``clear``) so backends ranging from the bundled in-memory store to an external
vector database can be dropped in interchangeably.
"""

from __future__ import annotations

import abc

from pydantic import BaseModel, Field

from forge.types import new_id


class MemoryItem(BaseModel):
    """A stored document and (after a search) its relevance score."""

    id: str = Field(default_factory=lambda: new_id("mem"))
    text: str
    metadata: dict[str, object] = Field(default_factory=dict)
    score: float | None = None


class Memory(abc.ABC):
    """A searchable long-term memory / retrieval backend."""

    @abc.abstractmethod
    async def add(self, text: str, *, metadata: dict[str, object] | None = None) -> str:
        """Store ``text`` and return its id."""

    @abc.abstractmethod
    async def search(self, query: str, *, k: int = 5) -> list[MemoryItem]:
        """Return up to ``k`` items most relevant to ``query``, most relevant first."""

    @abc.abstractmethod
    async def clear(self) -> None:
        """Remove all stored items."""
