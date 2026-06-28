"""The supervisor agent.

A :class:`Supervisor` implements the supervisor/worker pattern: it decomposes a
high-level goal into independent subtasks, spawns a focused worker
:class:`~forge.agents.agent.Agent` for each, then consolidates their outputs
into a single answer. Planning and synthesis are model-driven (so they benefit
from routing and are metered like any other call), with deterministic fallbacks
that keep the platform fully functional offline via the echo provider.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from forge.agents.agent import Agent
from forge.agents.base import AgentResult, BaseAgent
from forge.models.providers.echo import PLAN_MARKER, SYNTH_MARKER
from forge.models.router import Complexity
from forge.observability.events import EventType
from forge.tools.registry import ToolRegistry
from forge.types import Message, Usage

if TYPE_CHECKING:
    from forge.orchestration.context import RunContext

DEFAULT_WORKER_SYSTEM = (
    "You are a focused worker agent. Complete the single subtask you are given "
    "precisely and concisely. Use the available tools when they help."
)

_PLAN_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.*\S)\s*$")


class Supervisor(BaseAgent):
    """Decomposes a goal, delegates to workers, and synthesises the result."""

    def __init__(
        self,
        name: str,
        ctx: RunContext,
        *,
        worker_tools: ToolRegistry | None = None,
        worker_system_prompt: str | None = None,
        system_prompt: str | None = None,
        model_override: str | None = None,
        max_workers: int | None = None,
    ) -> None:
        super().__init__(
            name,
            ctx,
            system_prompt=system_prompt,
            tools=None,
            complexity=Complexity.HIGH,
            model_override=model_override,
        )
        self.worker_tools = worker_tools
        self.worker_system_prompt = worker_system_prompt or DEFAULT_WORKER_SYSTEM
        self.max_workers = max_workers if max_workers is not None else ctx.config.budget.max_workers

    async def run(self, goal: str) -> AgentResult:
        ctx = self._ctx
        ctx.events.emit(
            EventType.AGENT_STARTED, run_id=ctx.run_id, agent=self.name, task=goal[:200]
        )
        ctx.audit.record("agent.start", actor=self.name, run_id=ctx.run_id, resource=goal[:200])

        # 1) Plan.
        plan = await self._plan(goal)
        ctx.events.emit(EventType.PLAN_CREATED, run_id=ctx.run_id, agent=self.name, subtasks=plan)
        ctx.audit.record(
            "plan.created",
            actor=self.name,
            run_id=ctx.run_id,
            subtask_count=len(plan),
            subtasks=plan,
        )

        # 2) Delegate subtasks to workers, running each batch concurrently.
        children = await self._run_workers(plan)

        # 3) Synthesise a final answer and assemble the report.
        summary = await self._synthesize(goal, children)
        report = self._format_report(goal, plan, children, summary)

        ctx.events.emit(
            EventType.AGENT_FINISHED, run_id=ctx.run_id, agent=self.name, steps=len(plan)
        )
        ctx.audit.record(
            "agent.finish", actor=self.name, run_id=ctx.run_id, outcome="ok", steps=len(plan)
        )

        own = self._ctx.usage.report().by_agent.get(self.name, Usage())
        subtree_usage = sum((child.usage for child in children), own)
        return AgentResult(
            agent=self.name,
            output=report,
            usage=subtree_usage,
            steps=len(plan),
            children=children,
        )

    # ------------------------------------------------------------------ #
    # Parallel worker execution
    # ------------------------------------------------------------------ #
    async def _run_workers(self, plan: list[str]) -> list[AgentResult]:
        """Run the planned subtasks as workers, concurrently and in bounded batches.

        Subtasks are processed in batches of at most ``max_workers`` so parallelism
        stays bounded. Each batch runs under ``asyncio.gather(..., return_exceptions
        =True)`` so one failing worker never cancels its peers; a failure becomes a
        graceful, recorded error result instead of crashing the whole run. Results
        keep their subtask order (``gather`` preserves input order).
        """
        ctx = self._ctx
        # The model a worker will route to — used for the pre-flight budget estimate.
        worker_model = ctx.router.route(
            complexity=Complexity.MEDIUM, needs_tools=self.worker_tools is not None
        ).model

        children: list[AgentResult] = []
        for start in range(0, len(plan), self.max_workers):
            batch = plan[start : start + self.max_workers]

            # Pre-flight budget guard: refuse to spawn this batch if its worst-case
            # spend would blow the remaining budget (raises before any worker runs).
            ctx.preflight_budget(num_workers=len(batch), model_name=worker_model)

            coroutines: list[Coroutine[Any, Any, AgentResult]] = [
                self._run_worker(f"{self.name}.worker-{start + offset + 1}", subtask)
                for offset, subtask in enumerate(batch)
            ]
            results: list[AgentResult | BaseException] = await asyncio.gather(
                *coroutines, return_exceptions=True
            )

            for offset, result in enumerate(results):
                worker_id = f"{self.name}.worker-{start + offset + 1}"
                if isinstance(result, BaseException):
                    children.append(self._handle_worker_failure(worker_id, batch[offset], result))
                else:
                    children.append(result)
        return children

    async def _run_worker(self, worker_id: str, subtask: str) -> AgentResult:
        ctx = self._ctx
        ctx.events.emit(
            EventType.WORKER_STARTED,
            run_id=ctx.run_id,
            agent=self.name,
            worker_id=worker_id,
            subtask=subtask,
        )
        worker = Agent(
            worker_id,
            ctx,
            system_prompt=self.worker_system_prompt,
            tools=self.worker_tools,
            complexity=Complexity.MEDIUM,
        )
        return await worker.run(subtask)

    def _handle_worker_failure(
        self, worker_id: str, subtask: str, error: BaseException
    ) -> AgentResult:
        """Turn a worker exception into a recorded, graceful error result."""
        ctx = self._ctx
        ctx.events.emit(
            EventType.WORKER_FAILED,
            run_id=ctx.run_id,
            agent=self.name,
            worker_id=worker_id,
            subtask=subtask,
            error=str(error),
        )
        ctx.audit.record(
            "worker.failed",
            actor=self.name,
            run_id=ctx.run_id,
            outcome="error",
            resource=worker_id,
            subtask=subtask,
            error=str(error),
        )
        # Preserve any partial spend the worker incurred before it failed.
        usage = ctx.usage.report().by_agent.get(worker_id, Usage())
        return AgentResult(
            agent=worker_id,
            output=f"[failed] {subtask}: {error}",
            usage=usage,
            steps=0,
            success=False,
        )

    # ------------------------------------------------------------------ #
    # Planning & synthesis
    # ------------------------------------------------------------------ #
    async def _plan(self, goal: str) -> list[str]:
        prompt = (
            f"{PLAN_MARKER} You are a planning supervisor. Decompose the goal into a minimal, "
            "ordered list of independent subtasks. Output one subtask per line, each prefixed "
            f"with '- ', and nothing else.\n\nGoal: {goal}"
        )
        messages = self._with_system(prompt)
        response = await self._invoke_model(
            messages,
            complexity=Complexity.MEDIUM,
            override=self._ctx.config.routing.planner_model,
        )
        subtasks = self._parse_plan(response.content)
        if not subtasks:
            subtasks = [goal]
        return subtasks[: self.max_workers]

    async def _synthesize(self, goal: str, children: list[AgentResult]) -> str:
        results_block = "\n".join(
            f"- Subtask {i}: {child.output}" for i, child in enumerate(children, start=1)
        )
        prompt = (
            f"{SYNTH_MARKER} Consolidate the completed subtask results into a single, coherent, "
            f"concise final answer for the goal.\n\nGoal: {goal}\n\nSubtask results:\n{results_block}"
        )
        response = await self._invoke_model(self._with_system(prompt), complexity=Complexity.MEDIUM)
        return response.content

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _with_system(self, user_content: str) -> list[Message]:
        messages: list[Message] = []
        if self.system_prompt:
            messages.append(Message.system(self.system_prompt))
        messages.append(Message.user(user_content))
        return messages

    @staticmethod
    def _parse_plan(text: str) -> list[str]:
        subtasks: list[str] = []
        for line in text.splitlines():
            match = _PLAN_LINE.match(line)
            if match:
                subtasks.append(match.group(1).strip())
        return subtasks

    @staticmethod
    def _format_report(
        goal: str, plan: list[str], children: list[AgentResult], summary: str
    ) -> str:
        lines = [f"Goal: {goal}", "", "Plan:"]
        lines.extend(f"  {i}. {step}" for i, step in enumerate(plan, start=1))
        lines.append("")
        lines.append("Subtask results:")
        for i, child in enumerate(children, start=1):
            lines.append(f"  {i}. [{child.agent}] {child.output}")
        lines.append("")
        lines.append(f"Summary: {summary}")
        return "\n".join(lines)
