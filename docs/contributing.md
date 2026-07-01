# Contributing

Thanks for your interest in making Forge better. Forge aims to be a
**production-grade, enterprise-trustworthy** multi-agent platform, so we hold the
core to a high bar: typed, tested, and secure by default.

---

## Good first contributions

Not sure where to start? These areas are well-suited for first-time contributors:

- **New model provider** (Google Vertex, Gemini, Cohere, Mistral) -- implement one method (`ModelProvider.complete()`). See `forge/models/providers/anthropic.py` as the reference.
- **New built-in tool** (web search, file read, database query) -- add a `@tool` function under `forge/tools/builtin/`.
- **Durable memory backend** (Redis) -- implement `Memory` ABC (`add`/`search`/`clear`). SQLite and pgvector ship already; Redis is the next backend.
- **New routing strategy** -- add a strategy to `forge/models/router.py`. The interface is small and well-typed.
- **Example workflows** -- add a runnable script under `examples/` showing Forge solving a real business problem.

| Area | What it looks like |
|---|---|
| New providers | Implement `ModelProvider.complete` (see `forge/models/providers/`) |
| New tools | A `@tool`-decorated function in `forge/tools/builtin/`. Mark side-effecting ones `dangerous=True`. |
| Memory backends | Implement `Memory` (`add`/`search`/`clear`) over Redis, etc. |
| Routing strategies | Extend `ModelRouter` with new selection logic. |
| Docs and examples | Runnable scripts in `examples/`, or clarity fixes in the docs. |

---

## Ground rules

- **Keep it typed.** The core passes `mypy --strict`. New code should too.
- **Keep it tested.** Add tests for new behavior; the suite runs offline (no API
  key, no network) and must stay that way for unit tests.
- **Secure by default.** New tools that touch the network, filesystem, or other
  side effects must be marked `dangerous=True` so the sandbox gates them.
- **No secrets, ever.** Don't log API keys or PII; route sensitive values through
  the redactor.

All contributions must pass `pytest -q`, `mypy forge`, and `ruff check .` before merging.

---

## Development setup

```bash
git clone https://github.com/sekacorn/AgentForge.git
cd AgentForge
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

All four must be green before opening a PR.

---

## Architecture in 60 seconds

- **`Orchestrator`** is the entry point; it owns shared services and applies
  access control, sanitization, and accounting around every run.
- **Agents** (`Agent`, `Supervisor`) implement behavior on top of `BaseAgent`,
  which centralizes model invocation, routing, metering, and budget checks.
- **Cross-cutting concerns** -- the event bus, usage tracker, audit log, and
  redactor -- observe the run without the agents needing to know about them.

A new feature usually means: add/extend a component, register it, write a test
that exercises it via the `Orchestrator` (offline), and document it.

---

## Commit and PR conventions

- Write clear, imperative commit messages describing the change and the why.
- Keep PRs focused; smaller is easier to review and ship.
- Link any related issue and describe how you tested.

---

## Release process

Releases are published to PyPI automatically by GitHub Actions using a
[PyPI Trusted Publisher](https://docs.pypi.org/trusted-publishers/) (OIDC) -- no
API tokens are stored anywhere. To cut a release:

```bash
python scripts/bump_version.py X.Y.Z
git commit -m "chore: bump version to X.Y.Z"
git tag vX.Y.Z
git push && git push --tags
```

The `Release` workflow builds the package and publishes it to PyPI automatically.

---

## Code of Conduct

Be kind, be constructive, assume good faith. We want Forge to be a welcoming
project for contributors of every background and experience level.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](https://github.com/sekacorn/AgentForge/blob/main/LICENSE).
