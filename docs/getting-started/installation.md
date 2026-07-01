# Installation

## Requirements

- Python 3.11 or later
- pip (any recent version)

## Install from PyPI

The PyPI package is `agentforge-oss`. The import is `import forge` -- the same
convention as `pip install Pillow` then `import PIL`.

```bash
pip install agentforge-oss                       # core (works offline, zero config)
pip install "agentforge-oss[anthropic]"          # + Claude provider
pip install "agentforge-oss[openai]"             # + OpenAI / GPT provider
pip install "agentforge-oss[anthropic,openai]"   # both real providers
pip install "agentforge-oss[bedrock]"            # + Amazon Bedrock provider (boto3)
pip install "agentforge-oss[pgvector]"           # + PostgreSQL RAG (asyncpg + pgvector)
pip install "agentforge-oss[sqlite]"             # + SQLite persistent RAG (aiosqlite)
pip install "agentforge-oss[otel]"               # + OpenTelemetry tracing
pip install "agentforge-oss[all,dev]"            # everything + test/lint tooling
```

## Provider setup

### Echo (offline, zero config)

Forge works out of the box with no API key. The built-in Echo provider returns
deterministic responses so you can explore the full platform -- routing, tools,
supervision, audit -- without spending a cent or hitting the network.

### Anthropic (Claude)

Set your API key as an environment variable:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Available models: Claude Haiku 4.5, Sonnet 4.6, Opus 4.8, Fable 5.

### OpenAI (GPT)

```bash
export OPENAI_API_KEY="sk-..."
```

Available models: gpt-4o-mini, gpt-4o, gpt-4.1, o3.

### Ollama (local, free)

Ollama support is built into the core -- no extra install needed (it uses `httpx`,
already a core dependency, so there is no `[ollama]` extra).

```bash
ollama serve
ollama pull llama3.1:8b
```

Forge auto-detects a running Ollama server at `localhost:11434`. To point at a
different host:

```bash
export OLLAMA_BASE_URL="http://my-gpu-box:11434"
```

Available models: llama3.2:3b, llama3.1:8b, llama3.1:70b, mistral:7b, qwen2.5:7b, deepseek-r1:8b.

### Amazon Bedrock

```bash
pip install "agentforge-oss[bedrock]"
```

Bedrock uses the standard AWS credential chain (environment variables, named
profile, IAM role). No custom credential handling needed.

```bash
export AWS_REGION="us-east-1"
# or
export AWS_PROFILE="my-profile"
```

Available models: Claude 3.5 Haiku/Sonnet, Llama 3.1 70B/405B, Mistral Large --
all via the unified Converse API.

## Auto-selection priority

When multiple providers are available, Forge selects automatically in this order:

1. **Anthropic** (if `ANTHROPIC_API_KEY` is set)
2. **OpenAI** (if `OPENAI_API_KEY` is set)
3. **Bedrock** (if AWS credentials are configured)
4. **Ollama** (if a server is reachable)
5. **Echo** (always available, deterministic)

You can override this with `routing.default_provider` in your config.

## Model names

Forge uses registry aliases (e.g. `claude-sonnet-4-6`, `gpt-4o`) that map to
provider model IDs in `forge/models/registry.py`. Aliases can be updated as
provider IDs change without touching your code.

```bash
forge models   # see the full registry with aliases, tiers, and pricing
```

## Development install

```bash
git clone https://github.com/sekacorn/AgentForge.git
cd AgentForge
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[all,dev]"
```
