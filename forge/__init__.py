"""Forge (AgentForge) — an open-source, enterprise-ready multi-agent platform.

Quick start::

    import asyncio
    from forge import Orchestrator

    async def main() -> None:
        async with Orchestrator() as forge:           # zero-config, offline by default
            result = await forge.run("Research X and summarise the findings")
            print(result.output)
            print(result.usage.format_table())        # tokens + cost

    asyncio.run(main())

The most important entry point is :class:`Orchestrator`. The rest of the public
surface is re-exported here for convenience and stability.
"""

from __future__ import annotations

from forge._version import __version__
from forge.agents import Agent, AgentResult, BaseAgent, Supervisor
from forge.compliance import AuditEntry, AuditLogger, PIIRedactor
from forge.config import (
    BudgetConfig,
    ComplianceConfig,
    ForgeConfig,
    ObservabilityConfig,
    RoutingConfig,
    SecurityConfig,
)
from forge.exceptions import (
    AccessDeniedError,
    AgentError,
    BudgetExceededError,
    ConfigurationError,
    ForgeError,
    MaxStepsExceededError,
    ModelRoutingError,
    OrchestrationError,
    PromptInjectionError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    SecurityError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
    ToolValidationError,
)
from forge.memory import (
    ConversationMemory,
    InMemoryVectorStore,
    Memory,
    MemoryItem,
    SQLiteMemoryStore,
)
from forge.models import (
    Complexity,
    ModelInfo,
    ModelProvider,
    ModelRegistry,
    ModelRouter,
    ModelTier,
    RoutingDecision,
)
from forge.models.providers import (
    AnthropicProvider,
    EchoProvider,
    OllamaProvider,
    OpenAIProvider,
)
from forge.observability import (
    Event,
    EventBus,
    EventType,
    UsageReport,
    UsageTracker,
    configure_logging,
    get_logger,
)
from forge.orchestration import Orchestrator, RunContext, RunResult
from forge.security import (
    AccessController,
    InputSanitizer,
    Permission,
    Principal,
    ToolSandbox,
)
from forge.tools import SAFE_TOOLS, Tool, ToolRegistry, calculator, http_get, tool, utc_now
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

__all__ = [
    "__version__",
    "Orchestrator",
    "RunResult",
    "RunContext",
    "Agent",
    "Supervisor",
    "BaseAgent",
    "AgentResult",
    "ForgeConfig",
    "RoutingConfig",
    "BudgetConfig",
    "SecurityConfig",
    "ComplianceConfig",
    "ObservabilityConfig",
    "Role",
    "Message",
    "ToolCall",
    "ToolResult",
    "ToolSchema",
    "Usage",
    "ModelResponse",
    "FinishReason",
    "ModelProvider",
    "ModelRegistry",
    "ModelInfo",
    "ModelTier",
    "ModelRouter",
    "Complexity",
    "RoutingDecision",
    "EchoProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "OllamaProvider",
    "Tool",
    "tool",
    "ToolRegistry",
    "SAFE_TOOLS",
    "calculator",
    "http_get",
    "utc_now",
    "Memory",
    "MemoryItem",
    "ConversationMemory",
    "InMemoryVectorStore",
    "SQLiteMemoryStore",
    "Event",
    "EventBus",
    "EventType",
    "UsageTracker",
    "UsageReport",
    "get_logger",
    "configure_logging",
    "InputSanitizer",
    "ToolSandbox",
    "AccessController",
    "Principal",
    "Permission",
    "AuditLogger",
    "AuditEntry",
    "PIIRedactor",
    "ForgeError",
    "ConfigurationError",
    "ProviderError",
    "ProviderAuthError",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ModelRoutingError",
    "ToolError",
    "ToolNotFoundError",
    "ToolValidationError",
    "ToolExecutionError",
    "ToolTimeoutError",
    "AgentError",
    "MaxStepsExceededError",
    "OrchestrationError",
    "BudgetExceededError",
    "SecurityError",
    "PromptInjectionError",
    "AccessDeniedError",
]
