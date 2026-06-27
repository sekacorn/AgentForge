"""The base agent: shared model-invocation machinery.

:class:`BaseAgent` owns the one piece of logic every agent shares — turning a
list of messages into a model response while routing, metering cost, emitting
observability events, writing audit records, and enforcing the run budget.
Concrete agents (:class:`~forge.agents.agent.Agent`,
:class:`~forge.agents.supervisor.Supervisor`) implement ``run`` on top of it.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from forge.models.router import Complexity
from forge.observability.events import EventType
from forge.tools.registry import ToolRegistry
from forge.types import Message, ModelResponse, ToolSchema, Usage

if TYPE_CHECKING:
    # Imported only for typing to avoid a circular import at runtime
    # (orchestration -> agents -> base -> orchestration).
    from forge.orchestration.context import RunContext


class AgentResult(BaseModel):
    """The outcome of running an agent."""

    agent: str
    output: str
    usage: Usage = Field(default_factory=Usage)
    steps: int = 0
    success: bool = True
    #: Sub-results, populated by the supervisor for each worker it ran.
    children: list[AgentResult] = Field(default_factory=list)


class BaseAgent(abc.ABC):
    """Common base for all agents."""

    def __init__(
        self,
        name: str,
        ctx: RunContext,
        *,
        system_prompt: str | None = None,
        tools: ToolRegistry | None = None,
        complexity: Complexity = Complexity.MEDIUM,
        model_override: str | None = None,
        max_steps: int | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self.name = name
        self._ctx = ctx
        self.system_prompt = system_prompt
        self.tools = tools
        self.complexity = complexity
        self.model_override = model_override
        self.max_steps = (
            max_steps if max_steps is not None else ctx.config.budget.max_steps_per_agent
        )
        self.max_tokens = max_tokens

    @abc.abstractmethod
    async def run(self, task: str) -> AgentResult:
        """Execute the agent against ``task`` and return its result."""

    async def _invoke_model(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        complexity: Complexity | None = None,
        override: str | None = None,
    ) -> ModelResponse:
        """Route, call the model, meter usage, audit, and enforce budget."""
        ctx = self._ctx
        decision = ctx.router.route(
            complexity=complexity or self.complexity,
            needs_tools=bool(tools),
            override=override or self.model_override,
        )
        ctx.events.emit(
            EventType.MODEL_ROUTED,
            run_id=ctx.run_id,
            agent=self.name,
            model=decision.model,
            reason=decision.reason,
        )

        provider = ctx.provider_for(decision.model)
        info = ctx.registry.get(decision.model)
        max_tokens = min(self.max_tokens, info.max_output_tokens)

        ctx.events.emit(
            EventType.MODEL_CALL_STARTED, run_id=ctx.run_id, agent=self.name, model=decision.model
        )
        try:
            response = await provider.complete(
                messages, model=decision.model, tools=tools, max_tokens=max_tokens
            )
        except Exception as exc:
            # Surface model/provider failures on the event stream (symmetric with
            # tool failures) before letting the error propagate and be audited.
            ctx.events.emit(
                EventType.MODEL_CALL_FAILED,
                run_id=ctx.run_id,
                agent=self.name,
                model=decision.model,
                error=str(exc),
            )
            raise

        # Pricing is resolved centrally from the registry, never the provider.
        response.usage.cost_usd = info.cost(response.usage)
        ctx.usage.record(response.usage, agent=self.name, model=decision.model)

        ctx.events.emit(
            EventType.MODEL_CALL_FINISHED,
            run_id=ctx.run_id,
            agent=self.name,
            model=decision.model,
            tokens=response.usage.total_tokens,
            cost_usd=response.usage.cost_usd,
        )
        ctx.audit.record(
            "model.call",
            actor=self.name,
            resource=decision.model,
            run_id=ctx.run_id,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=response.usage.cost_usd,
        )

        # Halts a runaway run *before* the next expensive call.
        ctx.check_budget()
        return response
