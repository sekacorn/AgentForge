from __future__ import annotations

from forge import ConversationMemory, InMemoryVectorStore, Message


async def test_vector_store_ranks_by_relevance() -> None:
    store = InMemoryVectorStore()
    await store.add("Forge routes cheap tasks to small models to save cost.")
    await store.add("The audit log is hash-chained and tamper evident.")
    await store.add("Bananas are a good source of potassium.")

    hits = await store.search("how does forge reduce cost?", k=2)
    assert hits, "expected at least one hit"
    assert "cost" in hits[0].text.lower()
    assert hits[0].score is not None and hits[0].score > 0


async def test_vector_store_empty_search() -> None:
    store = InMemoryVectorStore()
    assert await store.search("anything") == []


def test_conversation_window_keeps_system_and_recent() -> None:
    memory = ConversationMemory(max_messages=2)
    memory.add(Message.system("rules"))
    for i in range(5):
        memory.add(Message.user(f"msg-{i}"))

    history = memory.history()
    # System message retained, plus only the 2 most recent user turns.
    assert history[0].content == "rules"
    assert [m.content for m in history[1:]] == ["msg-3", "msg-4"]
