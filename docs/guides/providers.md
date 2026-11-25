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
not a claim.** This project has not contacted any vendor's API. A preset marked
`reviewed` had its base URL read on the vendor's documentation page on the
recorded date, and nothing more; the provider itself is tested against a fake
in-process server only. [Provider coverage](../provider-coverage.md) lists the
status of each preset.

### Overriding a preset's URL

Set both. The URL wins, and the preset still supplies the key variable and the
token parameter:

```bash
export FORGE_OPENAI_COMPATIBLE_PRESET=moonshot
export FORGE_OPENAI_COMPATIBLE_BASE_URL=https://api.moonshot.ai/v1
```

Repointing a preset **resets its jurisdiction to `"unknown"`** and its hosting
is derived from the new URL. A preset describes its vendor's documented
endpoint, not wherever it was aimed, so the declaration does not follow you to
a different host.

### A custom endpoint with no preset

```bash
export FORGE_OPENAI_COMPATIBLE_BASE_URL=https://llm.example.com/v1
export FORGE_OPENAI_COMPATIBLE_API_KEY_ENV=MY_ENDPOINT_KEY
```

Its jurisdiction is `"unknown"` and its hosting is derived from the URL. If
you govern model use by jurisdiction, a policy that denies `unknown` will stop
this endpoint until you describe it with a preset of your own.

#### What a URL may contain

Every URL you configure follows one rule: the provider base URLs, the
`OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL` and `OLLAMA_BASE_URL` variables the
SDKs read, and the OpenTelemetry endpoint.

- **Credentials (`user:password@host`) are refused.** Supply the key through
  its environment variable.
- **A fragment (`#...`) is refused.**
- **A query string (`?...`) is refused.** HTTP clients log the request URL,
  and a token placed in a query string reaches every log line.

Each refusal is a configuration error that names the problem and never repeats
the URL.

#### Query parameters an endpoint requires

Some endpoints need a parameter on every request, such as an `api-version`.
Put it in `default_query` instead of the URL:

```bash
export FORGE_OPENAI_COMPATIBLE_BASE_URL=https://llm.example.com/v1
export FORGE_OPENAI_COMPATIBLE_DEFAULT_QUERY='api-version=2024-06-01'
```

or, in code, `OpenAICompatibleProvider(..., default_query={"api-version": "2024-06-01"})`
(and the same argument on `OpenAIProvider`).

The values are sent on every request and treated as secrets everywhere else:
they are `SecretStr` in the configuration, never part of the provider's
description or events, never in an error, and redacted from the log lines of
the HTTP client and the vendor SDKs. The variable is form-encoded
(`key=value&key=value`); a repeated or empty parameter name is refused. None of
the shipped presets needs one.

### Your own preset file

Describe your endpoints in a file with the same schema as the shipped presets
and point the configuration at it:

```json
{
  "schema_version": "provider-presets.v1",
  "presets": {
    "company_gateway": {
      "base_url": "https://llm.internal.example.com/v1",
      "docs_url": "https://docs.internal.example.com/llm",
      "jurisdiction": "DE",
      "hosting": "hosted",
      "api_key_env": "INTERNAL_LLM_KEY",
      "token_param": "max_tokens",
      "compatibility": "documented",
      "review": "unreviewed",
      "reviewed_on": null,
      "notes": ""
    }
  }
}
```

```bash
export FORGE_PROVIDER_PRESETS_PATH=/etc/forge/presets.json
export FORGE_OPENAI_COMPATIBLE_PRESET=company_gateway
```

Your presets are loaded alongside the built-in ones and validated the same
way: the declared hosting must agree with the URL, and only a local endpoint
may have no jurisdiction.

- **The jurisdiction you declare is reported as attested**
  (`jurisdiction_verification: "attested"`), exactly like the shipped presets.
  It is your statement, and nothing checks it.
- **An id that collides with a built-in preset is an error**, never an
  override. A silent override would change what a well-known name points at,
  and its jurisdiction with it, while every log and policy went on showing the
  familiar name. Choose a new id.
- Events record where a preset came from in `provider_preset_source`
  (`"built_in"` or `"user"`).

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
`jurisdiction`, for every provider, plus `jurisdiction_verification`. With a
preset the jurisdiction is a stated one, reported as `attested`: it says
nothing about where a packet travels. With a custom `base_url` the hosting is derived from the endpoint
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
