# Code Review Agent — quality charter & checklist

> **Role on the team:** *Agent 9 — Code Reviewer (Quality Assurance).* Every change
> to Forge passes this reviewer before it merges. Its mandate is simple: keep the
> codebase correct, consistent, secure, and a pleasure to extend — catching the
> things linters and tests don't.

The automated gates (`ruff`, `mypy --strict`, `pytest`) are necessary but not
sufficient. They prove the code is *well-formed and green*; the reviewer proves
it is *right and well-designed*. Use this checklist for every PR.

## How to run a review

1. **Gates first** — these must pass before human/agent review even starts:
   ```bash
   ruff check .        # lint + import order
   ruff format --check .
   mypy forge          # strict types
   pytest -q           # 33+ offline tests
   ```
2. **Diff review** — read the actual change. Maintainers using Claude Code can run
   `/code-review` on the branch diff to get an automated correctness/cleanup pass.
3. **Checklist** — walk the categories below. Report findings with a **severity**
   (blocker / major / minor / nit) and a concrete suggested fix.

## The checklist

### Correctness
- [ ] Edge cases handled: empty inputs, `None`, zero, very large values, unicode.
- [ ] No off-by-one or boundary bugs in loops/budgets/step limits.
- [ ] Async code: every awaitable is awaited; no blocking calls on the event loop.
- [ ] Determinism where it's promised (e.g. offline provider, vector store hashing).

### Types & API design
- [ ] `mypy --strict` clean; no stray `Any`, no unjustified `# type: ignore`.
- [ ] Public functions have precise signatures; overloads where call shapes differ.
- [ ] New public symbols are exported from the relevant `__init__` and `__all__`.
- [ ] Names read like the surrounding code; no surprising parameter ordering.

### Error handling
- [ ] Failures raise a specific `ForgeError` subclass (not bare `Exception`).
- [ ] Errors carry useful, **secret-free** `context`.
- [ ] Recoverable failures (tool errors) degrade gracefully; fatal ones surface.

### Security
- [ ] New tools that touch network/filesystem/state are marked `dangerous=True`.
- [ ] No `eval`/`exec`/shell-injection; untrusted input is sanitized or bounded.
- [ ] No secrets or PII in logs, audit records, exceptions, or test fixtures.
- [ ] Access-controlled operations check the right `Permission`.

### Observability & governance
- [ ] Significant lifecycle moments emit an `Event` (started/finished/**failed**).
- [ ] Metered model calls record usage/cost; nothing bypasses the budget check.
- [ ] Auditable actions write an audit record; the hash chain stays intact.

### Tests
- [ ] New behavior has a test; bug fixes add a regression test.
- [ ] Tests are offline/hermetic (no network, no API key) and deterministic.
- [ ] Both the happy path and at least one failure path are covered.

### Performance & simplicity
- [ ] No accidental O(n²) or repeated work in hot paths.
- [ ] The simplest design that meets the need — no speculative abstraction.
- [ ] Dependencies justified; the core stays light.

### Docs & DX
- [ ] Module/public-symbol docstrings explain *why*, not just *what*.
- [ ] README/examples updated if behavior or the public API changed.
- [ ] Comments match the code's density and idiom; no stale comments.

## Severity guide

| Severity | Meaning | Action |
|---|---|---|
| **Blocker** | Incorrect, insecure, or breaks the public contract | Must fix before merge |
| **Major** | Real bug risk, missing test, or design smell | Fix or explicitly defer with a tracked issue |
| **Minor** | Readability, naming, small inconsistency | Fix if cheap |
| **Nit** | Style preference | Optional |
