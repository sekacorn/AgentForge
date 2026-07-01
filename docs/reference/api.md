# API reference

The key public API surface. This covers the 80% a developer needs. Full
auto-generated API docs will be added in a future sprint.

---

## Orchestrator

The main entry point. Owns shared services and applies access control,
sanitization, and accounting around every run.

```python
from forge import Orchestrator, ForgeConfig

config = ForgeConfig(...)
async with Orchestrator(config) as forge:
    result = await forge.run("your goal here")
```

### `Orchestrator.__init__(config, *, providers=None)`

| Parameter | Type | Description |
|---|---|---|
| `config` | `ForgeConfig` | Configuration object (optional, defaults to `ForgeConfig()`) |
| `providers` | `dict[str, ModelProvider]` | Override auto-detected providers |

### `Orchestrator.run(goal, *, mode, tools, stream, principal, system_prompt, policy_set)`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `goal` | `str` | required | The task for agents to accomplish |
| `mode` | `str` | `"supervisor"` | `"single"` or `"supervisor"` |
| `tools` | `list[Tool] \| ToolRegistry` | `None` | Tools available to agents |
| `stream` | `bool` | `False` | Enable token streaming |
| `principal` | `Principal` | `None` | RBAC identity for access control |
| `system_prompt` | `str` | `None` | Custom system prompt for agents |
| `policy_set` | `PolicySet` | `None` | Policy-as-code rules for tool governance |

Returns a `RunResult`.

### `Orchestrator.subscribe(handler)`

Register an event handler for all lifecycle events.

### `Orchestrator.verify_audit()`

Returns `True` if the audit log hash chain is intact.

---

## RunResult

Returned by `Orchestrator.run()`.

| Field | Type | Description |
|---|---|---|
| `output` | `str` | The final text output |
| `usage` | `UsageReport` | Token and cost breakdown |

---

## ForgeConfig

Top-level configuration. See [Configuration reference](configuration.md) for all fields.

```python
from forge import ForgeConfig, BudgetConfig, RoutingConfig

config = ForgeConfig(
    routing=RoutingConfig(strategy="cost_optimized"),
    budget=BudgetConfig(max_usd_per_run=0.50),
)
```

### Factory methods

- `ForgeConfig.load(path=None)` -- load from TOML file + environment overlay
- `ForgeConfig.from_env()` -- environment variables only

---

## PolicyRule

A single governance rule evaluated before a tool call executes.

```python
from forge import PolicyRule

rule = PolicyRule(
    name="block-calculator",
    description="Calculator is blocked by policy",
    tool_names=["calculator"],
    condition=lambda name, args: True,
    action="deny",
)
```

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Unique rule name |
| `description` | `str` | Human-readable description |
| `tool_names` | `list[str]` | Tool names to match (`"*"` for wildcard) |
| `condition` | `Callable[[str, dict], bool]` | When to apply (receives tool name and args) |
| `action` | `Literal["approve", "deny", "log"]` | What to do when matched |
| `approver` | `Callable \| None` | Async callback for `approve` action |

---

## PolicySet

An ordered collection of `PolicyRule` instances.

```python
from forge import PolicySet

policy = PolicySet()
policy.add(rule)
decision = await policy.evaluate("calculator", {"expression": "2+2"})
```

### `PolicySet.evaluate(tool_name, args) -> PolicyDecision`

Evaluates all rules in order. `deny` and `approve` short-circuit; `log` continues.

---

## Memory ABC

The interface for all memory backends.

```python
from forge.memory.base import Memory, MemoryItem

class Memory(ABC):
    async def add(self, text: str, *, metadata: dict | None = None) -> str: ...
    async def search(self, query: str, *, k: int = 5) -> list[MemoryItem]: ...
    async def clear(self) -> None: ...
```

### MemoryItem

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique identifier |
| `text` | `str` | The stored text |
| `metadata` | `dict` | Optional structured metadata |
| `score` | `float` | Similarity score (0 to 1) |

### Implementations

- `InMemoryVectorStore` -- in-process, no persistence
- `SQLiteMemoryStore(path)` -- file-backed, async context manager
- `PGVectorMemoryStore(dsn)` -- PostgreSQL + pgvector, async context manager

---

## ModelProvider ABC

The interface for adding new LLM providers.

```python
from forge.models import ModelProvider

class ModelProvider(ABC):
    @property
    def name(self) -> str: ...

    @property
    def models(self) -> list[ModelInfo]: ...

    async def complete(self, messages, model, tools=None, **kwargs) -> ModelResponse: ...
```

### Shipped providers

- `EchoProvider` -- deterministic, offline
- `AnthropicProvider` -- Claude models
- `OpenAIProvider` -- GPT models
- `OllamaProvider` -- local models via Ollama REST API
- `BedrockProvider` -- AWS Bedrock Converse API

---

## @tool decorator

Turn a typed Python function into an agent tool:

```python
from forge import tool

@tool
def my_function(x: int, y: int) -> int:
    """Add two numbers.

    Args:
        x: First number.
        y: Second number.
    """
    return x + y
```

### `@tool(dangerous=True)`

Mark side-effecting tools so the sandbox gates them.

---

## Event / EventBus / EventType

```python
from forge import Event, EventBus, EventType

bus = EventBus()
bus.subscribe(lambda event: print(event.type.value))
bus.emit(EventType.RUN_STARTED, run_id="abc")
```

See [Observability guide](../guides/observability.md) for the full list of 25
event types.

---

## Principal / Permission / AccessController

```python
from forge import Principal

admin = Principal(id="admin-1", roles=frozenset({"admin"}))
viewer = Principal(id="viewer-1", roles=frozenset({"viewer"}))
```

Four roles: `admin`, `operator`, `developer`, `viewer`.
