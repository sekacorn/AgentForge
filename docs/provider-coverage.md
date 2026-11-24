# Provider coverage

What this project can say about each OpenAI-compatible endpoint it ships a
preset for, and what it cannot.

**No vendor's API has been contacted by this project.** Nothing on this page
means a vendor was tried against its real service. The words "verified",
"supported" and "works with" are deliberately absent.

## What the statuses mean

| Status | Means | Never means |
|---|---|---|
| `fake-tested` | A fake OpenAI-compatible server, running in-process, exercised the request and response shape: path, authorization header, JSON body, tools sent as functions, a tool call parsed back, and a tool-result round trip. | That any vendor's real API was contacted or behaves the same way. |
| `documented` | The preset's `base_url` was read on the vendor's own documentation page, at the link and on the date recorded. | That anything was executed against the vendor. |
| `untested` | The preset exists. Its `base_url` has not been confirmed against the vendor's documentation. | Anything further. |

`fake-tested` describes **the generic provider**, the one code path every
preset shares, not any individual vendor. The fake server does not imitate a
particular vendor, so no preset earns `fake-tested` on its own. Each preset's
row below therefore says `documented` or `untested`.

## The generic provider

| Component | Status | Evidence |
|---|---|---|
| `OpenAICompatibleProvider` request shape | `fake-tested` | `tests/test_openai_compatible_provider.py` |
| Tool calling (tools sent, tool call parsed, tool-result round trip) | `fake-tested` | same file |
| API key absent from errors and logs | `fake-tested` | same file, against a server that reflects the Authorization header |
| Streaming | not covered | The provider inherits the buffered default: one chunk, no incremental deltas. |

## Presets

Jurisdiction is the vendor's stated corporate jurisdiction. It is **attested**,
never checked, and says nothing about where a request is routed or stored.
Every hosted preset needs an API key in the environment variable shown.

| Preset | Hosting | Jurisdiction (attested) | Status | Documentation read | Limitations recorded |
|---|---|---|---|---|---|
| `mistral` | hosted | FR | `untested` | not confirmed | The base URL could not be read verbatim on the documentation page, so the preset is left unreviewed. |
| `deepseek` | hosted | CN | `documented` | [api-docs.deepseek.com](https://api-docs.deepseek.com/), 2026-11-24 | — |
| `moonshot` | hosted | CN | `documented` | [platform.kimi.com/docs](https://platform.kimi.com/docs), 2026-11-24 | The international documentation gives a different host; set `base_url` to use it. |
| `qwen` | hosted | CN | `documented`, partial | [help.aliyun.com](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope), 2026-11-24 | The documentation now recommends a workspace-specific domain and calls this one legacy. It also says tools cannot be combined with streaming. |
| `groq` | hosted | US | `documented` | [console.groq.com/docs/openai](https://console.groq.com/docs/openai), 2026-11-24 | The documentation lists unsupported request fields. |
| `together` | hosted | US | `documented` | [docs.together.ai](https://docs.together.ai/docs/openai-api-compatibility), 2026-11-24 | — |
| `fireworks` | hosted | US | `documented` | [docs.fireworks.ai](https://docs.fireworks.ai/tools-sdks/openai-compatibility), 2026-11-24 | `max_tokens` is lowered to fit the context window by default. |
| `gemini_openai` | hosted | US | `documented`, partial | [aistudio.google.com/docs/openai](https://aistudio.google.com/docs/openai), 2026-11-24 | A compatibility layer its documentation calls beta. |
| `local_vllm` | local | not applicable | `documented` | [docs.vllm.ai](https://docs.vllm.ai/en/latest/getting_started/quickstart/), 2026-11-24 | — |
| `local_lmstudio` | local | not applicable | `documented` | [lmstudio.ai/docs](https://lmstudio.ai/docs/developer/openai-compat), 2026-11-24 | — |
| `local_ollama_openai` | local | not applicable | `documented`, partial | [docs.ollama.com/openai](https://docs.ollama.com/openai), 2026-11-24 | Its documentation lists `tool_choice` as unsupported. The dedicated `ollama` provider uses the native API. |

"partial" means the vendor's own documentation describes a divergence from the
Chat Completions behaviour this provider assumes. It is recorded from the
documentation, not observed.

## What a review is

Each `documented` row was produced by reading the linked page and finding the
`base_url` on it. The review changed three presets: one vendor's documented
host had changed, and three documentation links had moved. A review does not
age well: vendors move endpoints and documentation. The `reviewed_on` date in
the preset data says how old each reading is.

## Your own endpoints

A custom `base_url` with no preset reports jurisdiction `"unknown"` and hosting
derived from the URL. A preset file you supply may declare a jurisdiction, and
it is reported as attested in exactly the same way as the shipped ones. See
the [providers guide](guides/providers.md#openai-compatible-endpoints).
