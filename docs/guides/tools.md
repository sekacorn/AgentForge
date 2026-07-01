# Tools

Tools let agents take actions -- compute, fetch data, transform text, call APIs.
Forge generates JSON schema automatically from type hints and docstrings, so agents
know what a tool does and how to call it.

---

## The @tool decorator

Turn any typed Python function into an agent tool:

```python
from forge import tool

@tool
def fx_convert(amount: float, rate: float) -> float:
    """Convert an amount of money using an exchange rate.

    Args:
        amount: The amount in the source currency.
        rate: The exchange rate to apply.
    """
    return round(amount * rate, 2)
```

The decorator reads your type annotations and docstring to build a JSON schema.
The agent sees a tool named `fx_convert` with two float parameters and a
description it can reason about.

### Rules for @tool functions

- **Type all parameters.** The schema is generated from annotations; untyped
  parameters break schema generation.
- **Write a docstring.** The first line becomes the tool description. An `Args:`
  section (Google-style) generates parameter descriptions.
- **Return a value.** The return value is serialized and shown to the agent as the
  tool result.

---

## Registering tools

Pass tools to the orchestrator via a `ToolRegistry`:

```python
from forge import Orchestrator, ToolRegistry, calculator, tool

@tool
def my_tool(x: int) -> int:
    """Double a number.

    Args:
        x: The number to double.
    """
    return x * 2

tools = ToolRegistry([calculator, my_tool])

async with Orchestrator() as forge:
    result = await forge.run("Double 21 and add 100", tools=tools, mode="single")
```

Or pass tools as a plain list to `Orchestrator.run()`:

```python
result = await forge.run("...", tools=[calculator, my_tool])
```

---

## Built-in tools

Forge ships three built-in tools:

### calculator

Evaluates a math expression safely.

```python
from forge import calculator

result = calculator.execute({"expression": "15 * 0.18"})
# ToolResult(output="2.7")
```

### utc_now

Returns the current UTC timestamp.

```python
from forge import utc_now

result = utc_now.execute({})
# ToolResult(output="2026-07-01T12:00:00+00:00")
```

### http_get

Fetches a URL. Marked `dangerous=True` -- blocked by the sandbox unless
explicitly allowed.

```python
from forge import http_get

# Only works if "http_get" is in the security allowlist
result = await http_get.execute({"url": "https://api.example.com/data"})
```

---

## Dangerous tools

Tools that access the network, filesystem, or other side effects must be marked
`dangerous=True`:

```python
@tool(dangerous=True)
def write_file(path: str, content: str) -> str:
    """Write content to a file.

    Args:
        path: The file path.
        content: The content to write.
    """
    with open(path, "w") as f:
        f.write(content)
    return f"Wrote {len(content)} bytes to {path}"
```

Dangerous tools are **denied by default** by the `ToolSandbox`. To allow them,
add them to the security allowlist:

```python
from forge import ForgeConfig, SecurityConfig

config = ForgeConfig(
    security=SecurityConfig(allow_tools=["write_file", "http_get"])
)
```

Or via environment / forge.toml:

```toml
[security]
allow_tools = ["write_file", "http_get"]
```

---

## Tool sandboxing

Every tool call runs through the `ToolSandbox`, which enforces:

- **Allowlist/denylist** -- only permitted tools can execute
- **Timeouts** -- each tool has a configurable execution timeout (default: 30s)
- **Dangerous-denied-by-default** -- side-effecting tools require explicit opt-in
- **Policy-as-code** -- if a `PolicySet` is attached, rules evaluate before execution

The sandbox records every call (allowed or denied) to the audit log and emits
events on the event bus.

---

## Tool timeout

Configure the per-tool timeout globally:

```python
config = ForgeConfig(
    security=SecurityConfig(tool_timeout_seconds=60)
)
```

Or via environment:

```bash
export FORGE_TOOL_TIMEOUT_SECONDS=60
```
