"""Tests for streaming token output through the event bus.

All hermetic: the offline ``EchoProvider`` simulates streaming by chunking its
deterministic completion, so no API key, network, or real delay is involved. The
``await asyncio.sleep(0)`` inside the provider yields to the event loop without
wall-clock time, keeping the whole module well under a second.
"""

from __future__ import annotations

from forge import (
    EchoProvider,
    EventType,
    FinishReason,
    ForgeConfig,
    Message,
    ModelProvider,
    ModelResponse,
    Orchestrator,
    ToolSchema,
    Usage,
)

_TOKEN_TYPES = {
    EventType.TOKEN_STREAM_START,
    EventType.TOKEN_CHUNK,
    EventType.TOKEN_STREAM_END,
}


class WholeResponseProvider(ModelProvider):
    """A provider that implements only ``complete`` and inherits the default
    buffered ``stream`` (which yields the whole response as a single chunk)."""

    name = "echo"  # maps onto the offline echo models in the registry

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        **options: object,
    ) -> ModelResponse:
        return ModelResponse(
            model=model,
            content="whole response text",
            finish_reason=FinishReason.STOP,
            usage=Usage(input_tokens=4, output_tokens=3),
        )


def _build(tmp_path, provider: ModelProvider) -> Orchestrator:
    cfg = ForgeConfig()
    cfg.compliance.audit_path = str(tmp_path / "audit.jsonl")
    return Orchestrator(cfg, providers={"echo": provider})


async def test_stream_false_emits_no_token_events(tmp_path):
    orchestrator = _build(tmp_path, EchoProvider())
    seen: list[EventType] = []
    orchestrator.subscribe(lambda event: seen.append(event.type))

    # Default path: stream defaults to False.
    result = await orchestrator.run("hello there", mode="single", tools=[])

    assert result.output  # the run completed normally
    assert not _TOKEN_TYPES.intersection(seen)  # no streaming events at all


async def test_stream_true_emits_ordered_token_events(tmp_path):
    orchestrator = _build(tmp_path, EchoProvider())
    events = []
    orchestrator.subscribe(events.append)

    result = await orchestrator.run("hello there", mode="single", tools=[], stream=True)

    token_events = [e for e in events if e.type in _TOKEN_TYPES]
    # Single mode makes exactly one model call -> exactly one stream.
    assert sum(1 for e in token_events if e.type is EventType.TOKEN_STREAM_START) == 1
    assert sum(1 for e in token_events if e.type is EventType.TOKEN_STREAM_END) == 1
    # START is first, END is last, everything between is a chunk.
    assert token_events[0].type is EventType.TOKEN_STREAM_START
    assert token_events[-1].type is EventType.TOKEN_STREAM_END
    assert all(e.type is EventType.TOKEN_CHUNK for e in token_events[1:-1])

    # START carries the model; END carries the full text.
    assert token_events[0].data.get("model")
    assert token_events[-1].data["text"] == result.output

    # Each chunk's cumulative text equals the running join of chunks so far.
    chunk_events = [e for e in token_events if e.type is EventType.TOKEN_CHUNK]
    assert chunk_events  # the echo response was streamed in multiple chunks
    running = ""
    for event in chunk_events:
        running += event.data["chunk"]
        assert event.data["text"] == running
    assert running == result.output


async def test_joined_chunks_equal_full_response(tmp_path):
    orchestrator = _build(tmp_path, EchoProvider())
    chunks: list[str] = []
    orchestrator.subscribe(
        lambda e: chunks.append(e.data["chunk"]) if e.type is EventType.TOKEN_CHUNK else None
    )

    result = await orchestrator.run(
        "summarize the onboarding flow", mode="single", tools=[], stream=True
    )

    assert "".join(chunks) == result.output


async def test_supervisor_streams_tagged_per_worker(tmp_path):
    orchestrator = _build(tmp_path, EchoProvider())
    events = []
    orchestrator.subscribe(events.append)

    await orchestrator.run("alpha and beta and gamma", mode="supervisor", tools=[], stream=True)

    starts = [e for e in events if e.type is EventType.TOKEN_STREAM_START]
    worker_agents = {
        e.agent for e in starts if e.agent and e.agent.startswith("supervisor.worker-")
    }
    # One stream per worker subtask (alpha / beta / gamma).
    assert len(worker_agents) == 3

    for worker in worker_agents:
        worker_chunks = [e for e in events if e.type is EventType.TOKEN_CHUNK and e.agent == worker]
        assert worker_chunks  # this worker streamed its own tokens
        # Per-worker chunk order is preserved even though workers run concurrently.
        assembled = "".join(e.data["chunk"] for e in worker_chunks)
        assert worker_chunks[-1].data["text"] == assembled


async def test_default_stream_yields_whole_response_as_one_chunk(tmp_path):
    # WholeResponseProvider does not override stream(): it uses the ABC default,
    # which buffers complete() and yields the entire content as a single chunk.
    orchestrator = _build(tmp_path, WholeResponseProvider())
    events = []
    orchestrator.subscribe(events.append)

    result = await orchestrator.run("anything", mode="single", tools=[], stream=True)

    chunk_events = [e for e in events if e.type is EventType.TOKEN_CHUNK]
    assert len(chunk_events) == 1
    assert chunk_events[0].data["chunk"] == "whole response text"
    assert result.output == "whole response text"
