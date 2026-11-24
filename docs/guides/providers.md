# Providers

Forge is provider-agnostic. Five providers ship in the box, and you can add any
provider by implementing one method (`ModelProvider.complete()`).

---

## Echo (offline, deterministic)

The default provider. Returns deterministic responses based on the input, so
every run is reproducible. Ideal for development, testing, and CI.

- **Install:** included in core (no extra)
- **API key:** none
- **Cost:** zero
- **Streaming:** native (word-by-word)

```python
from forge import Orchestrator

async with Orchestrator() as forge:
    result = await forge.run("Calculate 2 + 2", mode="single")
```

---

## Anthropic (Claude)

Claude Haiku 4.5, Sonnet 4.6, Opus 4.8, and Fable 5.

- **Install:** `pip install "agentforge-oss[anthropic]"`
- **API key:** `ANTHROPIC_API_KEY`

```python
import os

os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."

from forge import Orchestrator

async with Orchestrator() as forge:
    result = await forge.run("Explain quantum computing in one paragraph")
```

| Model alias | Tier | Input $/1M | Output $/1M |
|---|---|---|---|
| `claude-haiku-4-5` | standard | $1.00 | $5.00 |
| `claude-sonnet-4-6` | standard | $3.00 | $15.00 |
| `claude-opus-4-8` | frontier | $15.00 | $75.00 |
| `claude-fable-5` | frontier | $15.00 | $75.00 |

Run `forge models` for live pricing from the registry.

---

## OpenAI-compatible endpoints

Many vendors and local servers expose an endpoint that speaks the Chat
Completions API. One generic provider, `openai_compatible`, reaches all of
them. It needs the `openai` extra.

```bash
# A local server, by preset:
export FORGE_OPENAI_COMPATIBLE_PRESET=local_vllm

# Or any endpoint, by URL, with the key in a variable you name:
export FORGE_OPENAI_COMPATIBLE_BASE_URL=https://llm.example.com/v1
export FORGE_OPENAI_COMPATIBLE_API_KEY_ENV=MY_ENDPOINT_KEY
```

`FORGE_OPENAI_COMPATIBLE_API_KEY_ENV` is the **name** of the variable that
holds the key. The key itself is never written to configuration, and it never
appears in an error, a log line or an event.

Presets live in a versioned data file, `provider-presets.v1`. Each one records
a base URL, a link to the vendor's documentation, the vendor's stated
jurisdiction, and whether the endpoint is local or hosted. **A preset is data,
not a claim.** This project has not contacted any vendor's API; every preset is
marked `unreviewed`, and the provider is tested against a fake in-process
server only. Two presets are marked `partial` because their vendors document
known divergences.

The router only selects models that are registered. Register the models your
endpoint serves, with their real prices, under the provider name
`openai_compatible`:

```python
from forge import ModelInfo, ModelRegistry, ModelTier

registry = ModelRegistry()
registry.register(
    ModelInfo(
        name="my-model",
        provider="openai_compatible",
        tier=ModelTier.MEDIUM,
        context_window=32_000,
        max_output_tokens=4_096,
        input_cost_per_mtok=0.0,  # your endpoint's real price
        output_cost_per_mtok=0.0,
    )
)
```

No prices are invented for you: a model with no declared price would be
reported as free.

### Hosting and jurisdiction on every call

Every `model.call.started` event carries `provider`, `hosting` and
`jurisdiction`, for every provider. With a preset the jurisdiction is the
vendor's stated one, which is attested and says nothing about where a packet
travels. With a custom `base_url` the hosting is derived from the endpoint
(loopback and private ranges are local; names are never resolved) and the
jurisdiction is `"unknown"`. A provider this package knows nothing about is
`"unknown"` on both counts, never assumed local.

## OpenAI (GPT)

GPT-4o-mini, GPT-4o, GPT-4.1, and o3.

- **Install:** `pip install "agentforge-oss[openai]"`
- **API key:** `OPENAI_API_KEY`

```python
import os

os.environ["OPENAI_API_KEY"] = "sk-..."

from forge import Orchestrator

async with Orchestrator() as forge:
    result = await forge.run("Draft a product comparison table")
```

| Model alias | Tier | Input $/1M | Output $/1M |
|---|---|---|---|
| `gpt-4o-mini` | standard | $0.15 | $0.60 |
| `gpt-4o` | standard | $2.50 | $10.00 |
| `gpt-4.1` | frontier | $2.00 | $8.00 |
| `o3` | frontier | $10.00 | $40.00 |

---

## Ollama (local, free)

Run open-weight models locally with zero API cost. Air-gap compatible.

- **Install:** included in core (uses `httpx`, already a core dependency)
- **API key:** none
- **Setup:** install Ollama, pull a model, run `ollama serve`

```bash
ollama serve
ollama pull llama3.1:8b
```

```python
from forge import Orchestrator

# Set OLLAMA_BASE_URL to opt in to local Ollama routing.
async with Orchestrator() as forge:
    result = await forge.run("Summarize the benefits of local inference")
```

To point at a remote Ollama server:

```bash
export OLLAMA_BASE_URL="http://my-gpu-box:11434"
```

Available models: llama3.2:3b, llama3.1:8b, llama3.1:70b, mistral:7b, qwen2.5:7b, deepseek-r1:8b.

---

## Amazon Bedrock

Claude, Llama, and Mistral models running in your own AWS account via the
Converse API. GovCloud compatible.

- **Install:** `pip install "agentforge-oss[bedrock]"`
- **Credentials:** standard AWS credential chain (env vars, profile, IAM role)

```bash
export AWS_REGION="us-east-1"
```

```python
from forge import Orchestrator

async with Orchestrator() as forge:
    result = await forge.run("Analyze quarterly revenue trends")
```

Bedrock uses the standard AWS credential chain -- no custom credential handling.
Set `AWS_PROFILE` for a named profile, or let IAM roles handle it in production.

---

## Auto-selection priority

When multiple providers are available, Forge selects in this order:

1. **Anthropic** (if `ANTHROPIC_API_KEY` is set)
2. **OpenAI** (if `OPENAI_API_KEY` is set)
3. **Bedrock** (if AWS credentials are configured)
4. **Ollama** (if a server is reachable)
5. **Echo** (always available)

Override with `routing.default_provider` in `forge.toml` or `ForgeConfig`.

---

## Adding a custom provider

Implement the `ModelProvider` abstract base class:

```python
from forge.models import ModelProvider, ModelResponse, ModelInfo


class MyProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "my-provider"

    @property
    def models(self) -> list[ModelInfo]:
        return [...]  # your model registry entries

    async def complete(self, messages, model, tools=None, **kwargs):
        # Call your API here
        return ModelResponse(...)
```

Pass it to the orchestrator:

```python
async with Orchestrator(config, providers={"my-provider": MyProvider()}) as forge:
    ...
```
