"""Short-term conversation memory.

Holds the ordered transcript an agent is working through. A pinned system
message is preserved across windowing so the agent never loses its instructions,
while older turns can be bounded with ``max_messages`` to control context growth
and cost.
"""

from __future__ import annotations

from forge.types import Message, Role


class ConversationMemory:
    """An ordered buffer of conversation messages."""

    def __init__(self, *, max_messages: int | None = None) -> None:
        #: When set, the transcript is trimmed to the most recent N turns
        #: (the system message is always retained).
        self.max_messages = max_messages
        self._messages: list[Message] = []

    def add(self, message: Message) -> None:
        self._messages.append(message)
        self._truncate()

    def extend(self, messages: list[Message]) -> None:
        self._messages.extend(messages)
        self._truncate()

    def history(self) -> list[Message]:
        """The full retained transcript."""
        return list(self._messages)

    def last(self) -> Message | None:
        return self._messages[-1] if self._messages else None

    def clear(self) -> None:
        self._messages.clear()

    def _truncate(self) -> None:
        if self.max_messages is None or len(self._messages) <= self.max_messages:
            return
        system = [m for m in self._messages if m.role == Role.SYSTEM]
        recent = [m for m in self._messages if m.role != Role.SYSTEM][-self.max_messages :]
        self._messages = system + recent

    def __len__(self) -> int:
        return len(self._messages)
