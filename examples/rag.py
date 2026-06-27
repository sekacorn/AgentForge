"""Retrieval-augmented generation (RAG), fully offline.

Documents are embedded into the bundled in-memory vector store, the most
relevant ones are retrieved for a question, and the agent is grounded in that
context. The retrieval is real and deterministic; with a real model the final
answer would be synthesized from the retrieved facts.

Run:
    python examples/rag.py
"""

from __future__ import annotations

import asyncio

from forge import InMemoryVectorStore, Orchestrator

KNOWLEDGE = [
    "Forge denies dangerous tools (network and filesystem) unless explicitly allowlisted.",
    "Forge's audit log is append-only and SHA-256 hash-chained, so tampering is detectable.",
    "The model router supports cost_optimized, quality_first, balanced and fixed strategies.",
    "Per-run budgets cap spend in USD and tokens and halt a run before the next call.",
]


async def main() -> None:
    store = InMemoryVectorStore()
    for fact in KNOWLEDGE:
        await store.add(fact)

    question = "How does Forge keep tool use safe?"
    hits = await store.search(question, k=2)
    context = "\n".join(f"- {hit.text}  (score={hit.score:.3f})" for hit in hits)
    print(f"Question: {question}\n\nRetrieved context:\n{context}\n")

    system_prompt = (
        "You are a precise assistant. Answer using ONLY the provided context.\n\n"
        "Context:\n" + "\n".join(f"- {hit.text}" for hit in hits)
    )
    async with Orchestrator() as forge:
        result = await forge.run(question, mode="single", system_prompt=system_prompt)
    print("Answer:", result.output)


if __name__ == "__main__":
    asyncio.run(main())
