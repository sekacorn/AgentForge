"""Usage and cost tracking.

A :class:`UsageTracker` accumulates token usage and dollar cost across a run,
broken down by agent and by model. This is the backbone of Forge's cost-awareness
story: it powers budget enforcement, the end-of-run cost report, and the data
that gets written to audit logs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from forge.types import Usage, utcnow


class UsageRecord(BaseModel):
    """A single metered model call."""

    timestamp: object = Field(default_factory=utcnow)
    agent: str
    model: str
    usage: Usage


class UsageReport(BaseModel):
    """A serializable snapshot of accumulated usage."""

    total: Usage
    by_agent: dict[str, Usage]
    by_model: dict[str, Usage]
    num_calls: int

    def format_table(self) -> str:
        """Render a compact human-readable cost summary."""
        lines = [
            f"Total: {self.total.total_tokens:,} tokens "
            f"(in={self.total.input_tokens:,}, out={self.total.output_tokens:,}) "
            f"= ${self.total.cost_usd:.4f} across {self.num_calls} call(s)",
        ]
        if self.by_model:
            lines.append("By model:")
            for model, usage in sorted(self.by_model.items()):
                lines.append(f"  - {model}: {usage.total_tokens:,} tok / ${usage.cost_usd:.4f}")
        if self.by_agent:
            lines.append("By agent:")
            for agent, usage in sorted(self.by_agent.items()):
                lines.append(f"  - {agent}: {usage.total_tokens:,} tok / ${usage.cost_usd:.4f}")
        return "\n".join(lines)


class UsageTracker:
    """Accumulates :class:`Usage` across a run.

    Not thread-safe by design — a single run executes on one event loop. For
    concurrent runs, use one tracker per run (the orchestrator does this).
    """

    def __init__(self) -> None:
        self._total = Usage()
        self._by_agent: dict[str, Usage] = {}
        self._by_model: dict[str, Usage] = {}
        self._records: list[UsageRecord] = []

    def record(self, usage: Usage, *, agent: str, model: str) -> None:
        """Add one metered model call to the running totals."""
        self._total = self._total + usage
        self._by_agent[agent] = self._by_agent.get(agent, Usage()) + usage
        self._by_model[model] = self._by_model.get(model, Usage()) + usage
        self._records.append(UsageRecord(agent=agent, model=model, usage=usage))

    @property
    def total(self) -> Usage:
        return self._total

    @property
    def records(self) -> list[UsageRecord]:
        return list(self._records)

    def report(self) -> UsageReport:
        """Produce an immutable snapshot of the current totals."""
        return UsageReport(
            total=self._total,
            by_agent=dict(self._by_agent),
            by_model=dict(self._by_model),
            num_calls=len(self._records),
        )
