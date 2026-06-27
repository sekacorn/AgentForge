"""Tool sandboxing and policy enforcement.

Every tool call an agent makes is mediated by a :class:`ToolSandbox`, which:

* enforces an allowlist/denylist,
* **denies dangerous (side-effecting) tools by default** unless explicitly
  allowed,
* bounds execution time, and
* converts failures into structured, non-fatal :class:`ToolResult` errors so a
  misbehaving tool degrades gracefully instead of crashing the run.

The sandbox is a policy boundary, not OS-level isolation. It restricts *which*
tools run and *for how long*; for untrusted code execution you would additionally
run tools in a container or subprocess. The design makes that straightforward to
add behind the same interface.
"""

from __future__ import annotations

import asyncio

from forge.config import SecurityConfig
from forge.exceptions import ToolError
from forge.observability.events import EventBus, EventType
from forge.tools.base import Tool
from forge.types import ToolCall, ToolResult


class ToolSandbox:
    """Mediates tool execution according to a :class:`SecurityConfig`."""

    def __init__(
        self,
        config: SecurityConfig | None = None,
        *,
        events: EventBus | None = None,
    ) -> None:
        self._config = config or SecurityConfig()
        self._events = events

    def is_allowed(self, tool: Tool) -> tuple[bool, str | None]:
        """Decide whether ``tool`` may run. Returns ``(allowed, reason_if_not)``."""
        cfg = self._config
        if tool.name in cfg.block_tools:
            return False, "tool is on the denylist"
        if cfg.allow_tools is not None and tool.name not in cfg.allow_tools:
            return False, "tool is not on the allowlist"
        if tool.dangerous and not (cfg.allow_tools and tool.name in cfg.allow_tools):
            return False, "dangerous tools require explicit allowlisting"
        return True, None

    async def run(
        self,
        tool: Tool,
        call: ToolCall,
        *,
        agent: str | None = None,
        run_id: str | None = None,
    ) -> ToolResult:
        """Execute ``call`` against ``tool`` under policy and a timeout.

        Always returns a :class:`ToolResult`; failures are reported with
        ``is_error=True`` rather than raised, so the agent can recover.
        """
        allowed, reason = self.is_allowed(tool)
        if not allowed:
            self._emit(EventType.SECURITY_VIOLATION, run_id, agent, tool=tool.name, reason=reason)
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"Denied by policy: {reason}.",
                is_error=True,
            )

        self._emit(
            EventType.TOOL_CALL_STARTED, run_id, agent, tool=tool.name, arguments=call.arguments
        )
        try:
            content = await asyncio.wait_for(
                tool.invoke(call.arguments), timeout=self._config.tool_timeout_seconds
            )
        except TimeoutError:
            self._emit(EventType.TOOL_CALL_FAILED, run_id, agent, tool=tool.name, error="timeout")
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"Tool timed out after {self._config.tool_timeout_seconds}s.",
                is_error=True,
            )
        except ToolError as exc:
            self._emit(EventType.TOOL_CALL_FAILED, run_id, agent, tool=tool.name, error=str(exc))
            return ToolResult(
                tool_call_id=call.id, name=call.name, content=f"Error: {exc}", is_error=True
            )
        except Exception as exc:  # noqa: BLE001 - contain arbitrary tool failures
            self._emit(EventType.TOOL_CALL_FAILED, run_id, agent, tool=tool.name, error=str(exc))
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"Unexpected tool error: {exc}",
                is_error=True,
            )

        self._emit(EventType.TOOL_CALL_FINISHED, run_id, agent, tool=tool.name)
        return ToolResult(tool_call_id=call.id, name=call.name, content=content, is_error=False)

    def _emit(
        self, event_type: EventType, run_id: str | None, agent: str | None, **data: object
    ) -> None:
        if self._events is not None:
            self._events.emit(event_type, run_id=run_id, agent=agent, **data)
