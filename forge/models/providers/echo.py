"""A deterministic, offline model provider.

``EchoProvider`` needs no API key and never makes a network call, which makes it
ideal for tests, CI, local development, and the zero-setup quickstart. It is not
a language model — it follows a small set of deterministic rules — but it
implements enough behaviour to exercise the *whole* platform end to end:

* It emits a **tool call** when a calculator tool is available and the prompt
  contains an arithmetic expression, then produces a final answer once the tool
  result comes back. This drives the full agentic loop.
* It supports a lightweight **planning** contract so a supervisor can decompose
  a goal into subtasks without a real model.
* Otherwise it returns a concise, deterministic acknowledgement.

Token counts are estimated by character length so usage/cost accounting behaves
realistically (cost still resolves to $0 because echo models are free-tier).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

from forge.models.base import ModelProvider, split_system
from forge.types import (
    FinishReason,
    Message,
    ModelResponse,
    Role,
    ToolCall,
    ToolResult,
    ToolSchema,
    Usage,
)

# Markers the supervisor embeds in planning / synthesis prompts; they let echo
# branch deterministically while a real model just reads them as plain
# instruction text.
PLAN_MARKER = "[FORGE:PLAN]"
SYNTH_MARKER = "[FORGE:SYNTH]"

_BULLET = re.compile(r"^\s*(?:\d+\.|[-*])\s+", re.MULTILINE)

_ARITHMETIC = re.compile(r"[-+]?\d[\d\s.]*\s*[-+*/]\s*[\d\s.()+\-*/]*\d")
_SPLIT_PATTERN = re.compile(r"\s+and\s+|\s+then\s+|[;\n]+", re.IGNORECASE)

#: Characters per simulated streaming chunk (small, to exercise multi-chunk streams).
_STREAM_CHUNK_SIZE = 8


def _estimate_tokens(text: str) -> int:
    """Rough 4-chars-per-token estimate; good enough for demo accounting."""
    return max(1, len(text) // 4)


class EchoProvider(ModelProvider):
    """Offline deterministic provider (see module docstring)."""

    name = "echo"

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
        system_from_msgs, convo = split_system(messages)
        tool_names = {t.name for t in (tools or [])}

        input_tokens = _estimate_tokens(
            (system or "") + (system_from_msgs or "") + "".join(self._render(m) for m in messages)
        )

        # 1) If the most recent turn delivered tool results, synthesize a final
        #    answer from them — this terminates the agentic loop.
        last = convo[-1] if convo else None
        if last is not None and last.role == Role.TOOL and last.tool_results:
            content = self._synthesize(last.tool_results)
            return self._respond(model, content, input_tokens)

        user_text = self._latest_user_text(convo)

        # 2) Synthesis contract (supervisor consolidating worker results).
        if SYNTH_MARKER in user_text:
            return self._respond(model, self._synthesize_text(user_text), input_tokens)

        # 3) Planning contract.
        if PLAN_MARKER in user_text or (system_from_msgs and PLAN_MARKER in system_from_msgs):
            content = self._plan(user_text)
            return self._respond(model, content, input_tokens)

        # 4) Tool use: emit a calculator call for arithmetic, once.
        if "calculator" in tool_names and not self._already_used_calculator(convo):
            match = _ARITHMETIC.search(user_text)
            if match:
                expression = match.group(0).strip()
                call = ToolCall(name="calculator", arguments={"expression": expression})
                out_tokens = _estimate_tokens(expression) + 8
                return ModelResponse(
                    model=model,
                    content="",
                    tool_calls=[call],
                    finish_reason=FinishReason.TOOL_CALLS,
                    usage=Usage(input_tokens=input_tokens, output_tokens=out_tokens),
                )

        # 5) Default: a concise deterministic acknowledgement.
        content = self._acknowledge(user_text)
        return self._respond(model, content, input_tokens)

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        **options: Any,
    ) -> AsyncIterator[str]:
        """Simulate streaming by emitting the completion in small text chunks.

        This makes the offline provider behave like a real streaming model so the
        whole streaming path can be exercised in tests without an API key. The
        ``await asyncio.sleep(0)`` between chunks yields control to the event loop
        (so concurrent streams interleave) without adding any wall-clock delay.
        """
        response = await self.complete(
            messages, model=model, tools=tools, system=system, max_tokens=max_tokens, **options
        )
        content = response.content
        for start in range(0, len(content), _STREAM_CHUNK_SIZE):
            yield content[start : start + _STREAM_CHUNK_SIZE]
            await asyncio.sleep(0)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _render(message: Message) -> str:
        parts = [message.content]
        parts.extend(tc.name + str(tc.arguments) for tc in message.tool_calls)
        parts.extend(tr.content for tr in message.tool_results)
        return " ".join(p for p in parts if p)

    @staticmethod
    def _latest_user_text(convo: list[Message]) -> str:
        for message in reversed(convo):
            if message.role == Role.USER and message.content:
                return message.content
        return ""

    @staticmethod
    def _already_used_calculator(convo: list[Message]) -> bool:
        return any(tc.name == "calculator" for m in convo for tc in m.tool_calls)

    @staticmethod
    def _synthesize(results: list[ToolResult]) -> str:
        summary = "; ".join(f"{r.name}={r.content}" for r in results)
        return f"Done. Based on tool results ({summary}), the task is complete."

    def _plan(self, goal_text: str) -> str:
        goal = goal_text.replace(PLAN_MARKER, "").strip()
        # Strip a leading "Goal:" label if present.
        if "Goal:" in goal:
            goal = goal.split("Goal:", 1)[1].strip()
        chunks = [c.strip(" .") for c in _SPLIT_PATTERN.split(goal) if c.strip(" .")]
        if not chunks:
            chunks = [goal or "Complete the requested task"]
        return "\n".join(f"- {chunk}" for chunk in chunks)

    @staticmethod
    def _synthesize_text(text: str) -> str:
        goal = ""
        if "Goal:" in text:
            goal = text.split("Goal:", 1)[1].strip().splitlines()[0].strip()
        count = len(_BULLET.findall(text))
        suffix = f" across {count} subtask result(s)" if count else ""
        goal_part = f" for: {goal}" if goal else ""
        return f"Synthesized a consolidated final answer{goal_part}{suffix}."

    @staticmethod
    def _acknowledge(user_text: str) -> str:
        snippet = user_text.strip().splitlines()[0] if user_text.strip() else "the request"
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        return f"Acknowledged: {snippet}"

    @staticmethod
    def _respond(model: str, content: str, input_tokens: int) -> ModelResponse:
        return ModelResponse(
            model=model,
            content=content,
            finish_reason=FinishReason.STOP,
            usage=Usage(input_tokens=input_tokens, output_tokens=_estimate_tokens(content)),
        )
