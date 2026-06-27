# Contributing to Forge

Thanks for your interest in making Forge better! Forge aims to be a
**production-grade, enterprise-trustworthy** multi-agent platform, so we hold the
core to a high bar: typed, tested, and secure by default. This guide gets you
productive fast.

## Ground rules

- **Keep it typed.** The core passes `mypy --strict`. New code should too.
- **Keep it tested.** Add tests for new behavior; the suite runs offline (no API
  key, no network) and must stay that way for unit tests.
- **Secure by default.** New tools that touch the network, filesystem, or other
  side effects must be marked `dangerous=True` so the sandbox gates them.
- **No secrets, ever.** Don't log API keys or PII; route sensitive values through
  the redactor.

## Development setup

```bash
git clone https://github.com/agentforge/forge
cd forge
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[all,dev]"
```

## The dev loop

```bash
pytest            # fast, hermetic, offline
ruff check .      # lint
ruff format .     # format
mypy forge        # strict types
```

Please make sure all four are green before opening a PR.

## Where to start

Great first contributions, roughly in order of impact:

| Area | What it looks like |
|---|---|
| **New providers** | Implement `ModelProvider.complete` (see `forge/models/providers/`). OpenAI, Bedrock, Vertex, Ollama all welcome. |
| **New tools** | A `@tool`-decorated function in `forge/tools/builtin/`. Mark side-effecting ones `dangerous=True`. |
| **Memory backends** | Implement `Memory` (`add`/`search`/`clear`) over pgvector, Redis, etc. |
| **Routing strategies** | Extend `ModelRouter` with new selection logic. |
| **Docs & examples** | Runnable scripts in `examples/`, or clarity fixes in the README. |

## Architecture in 60 seconds

- **`Orchestrator`** is the entry point; it owns shared services and applies
  access control, sanitization, and accounting around every run.
- **Agents** (`Agent`, `Supervisor`) implement behavior on top of `BaseAgent`,
  which centralizes model invocation, routing, metering, and budget checks.
- **Cross-cutting concerns** — the event bus, usage tracker, audit log, and
  redactor — observe the run without the agents needing to know about them.

A new feature usually means: add/extend a component, register it, write a test
that exercises it via the `Orchestrator` (offline), and document it.

## Commit & PR conventions

- Write clear, imperative commit messages describing the change and the why.
- Keep PRs focused; smaller is easier to review and ship.
- Link any related issue and describe how you tested.

## Code of Conduct

Be kind, be constructive, assume good faith. We want Forge to be a welcoming
project for contributors of every background and experience level.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
