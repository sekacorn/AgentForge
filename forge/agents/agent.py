"""The worker agent and its agentic loop.

An :class:`Agent` runs the classic reason-act loop: call the model, and if it
requests tools, execute them (through the sandbox), feed the results back, and
repeat until the model produces a final answer or the step budget is exhausted.
Every tool call is policy-checked and audited; every model call is metered.
"""

from __future__ import annotations

from forge.agents.base import AgentResult, BaseAgent
from forge.exceptions import MaxStepsExceededError
from forge.observability.events import EventType
from forge.types import Message, ToolCall, ToolResult, Usage


class Agent(BaseAgent):
    """A single tool-using agent that completes one task."""

    async def run(self, task: str) -> AgentResult:
        ctx = self._ctx
        transcript: list[Message] = []
        if self.system_prompt:
            transcript.append(Message.system(self.system_prompt))
        transcript.append(Message.user(task))

        ctx.events.emit(
            EventType.AGENT_STARTED, run_id=ctx.run_id, agent=self.name, task=task[:200]
        )
        ctx.audit.record("agent.start", actor=self.name, run_id=ctx.run_id, resource=task[:200])

        tool_schemas = self.tools.schemas() if self.tools else None
        steps = 0
        for _ in range(self.max_steps):
            steps += 1
            response = await self._invoke_model(transcript, tools=tool_schemas)

            if response.has_tool_calls:
                transcript.append(Message.assistant(response.content, response.tool_calls))
                results = [await self._run_tool(call) for call in response.tool_calls]
                transcript.append(Message.tool(results))
                continue

            transcript.append(Message.assistant(response.content))
            ctx.events.emit(
                EventType.AGENT_FINISHED, run_id=ctx.run_id, agent=self.name, steps=steps
            )
            ctx.audit.record(
                "agent.finish", actor=self.name, run_id=ctx.run_id, outcome="ok", steps=steps
            )
            return AgentResult(
                agent=self.name, output=response.content, usage=self._own_usage(), steps=steps
            )

        # Loop exhausted without a final answer.
        ctx.events.emit(
            EventType.AGENT_FAILED, run_id=ctx.run_id, agent=self.name, reason="max_steps_exceeded"
        )
        ctx.audit.record(
            "agent.finish",
            actor=self.name,
            run_id=ctx.run_id,
            outcome="error",
            error="max_steps_exceeded",
            steps=steps,
        )
        raise MaxStepsExceededError(
            f"Agent {self.name!r} did not finish within {self.max_steps} steps",
            context={"agent": self.name, "max_steps": self.max_steps},
        )

    async def _run_tool(self, call: ToolCall) -> ToolResult:
        """Execute one tool call through the sandbox, auditing the outcome."""
        ctx = self._ctx
        if self.tools is None or not self.tools.has(call.name):
            ctx.events.emit(
                EventType.TOOL_CALL_FAILED,
                run_id=ctx.run_id,
                agent=self.name,
                tool=call.name,
                error="unknown tool",
            )
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"Error: unknown tool {call.name!r}.",
                is_error=True,
            )
        else:
            result = await ctx.sandbox.run(
                self.tools.get(call.name), call, agent=self.name, run_id=ctx.run_id
            )

        ctx.audit.record(
            "tool.call",
            actor=self.name,
            resource=call.name,
            run_id=ctx.run_id,
            outcome="error" if result.is_error else "ok",
            arguments=call.arguments,
        )
        return result

    def _own_usage(self) -> Usage:
        """This agent's share of the run's metered usage."""
        return self._ctx.usage.report().by_agent.get(self.name, Usage())
