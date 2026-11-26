# MCP tool connectors

Forge can use tools served by [Model Context Protocol](https://modelcontextprotocol.io)
servers. The connector speaks the **2026-07-28** protocol revision and treats
every server as untrusted: a tool is offered to a model only after a person
approved its exact definition, and it is refused again the moment that
definition changes.

The connector is an optional extra. Core Forge never imports the `mcp` SDK.

```bash
pip install "agentforge-oss[mcp]"
```

## Connecting

```python
from pathlib import Path

from forge import Orchestrator, ToolRegistry
from forge.config import ForgeConfig, SecurityConfig
from forge.tools.mcp import McpConnector, McpServerConfig, ToolApprovals

files = McpServerConfig(
    server_id="files",
    transport="streamable_http",
    url="http://127.0.0.1:8765/mcp",
)
approvals = ToolApprovals.load(Path("tool-approvals.json"))

async with McpConnector(files, approvals) as connector:
    tools = ToolRegistry(connector.tools())  # approved tools only
    config = ForgeConfig(security=SecurityConfig(allow_tools=tools.names()))
    async with Orchestrator(config) as forge:
        result = await forge.run("Find the refund policy", tools=tools, mode="single")
```

A model sees each tool as `<server_id>__<tool>` (for example
`files__search`). The same tool name on two servers is two tools, with two
identities and two approvals.

Every MCP tool is `dangerous=True`, so it also has to be on the sandbox
allowlist (`SecurityConfig.allow_tools`).

### Transports

| `transport` | Fields | Notes |
|---|---|---|
| `streamable_http` | `url`, `default_query`, `bearer_token_env` | The URL follows the endpoint rule: no credentials, query or fragment. Parameters an endpoint needs go in `default_query` and are redacted from logs. A bearer token is read from the environment variable named by `bearer_token_env`, never configured by value. |
| `stdio` | `command`, `args`, `env_allowlist` | Launched from an argument list, without a shell. The server receives only a small base environment plus the variables named in `env_allowlist`. |
| `in_process` | (a `server=` object) | For tests and embedded servers. |

Records name an HTTP server by **origin only** (`http://127.0.0.1:8765`), and
its hosting (`local` or `hosted`) is derived from that origin by the same rule
Forge uses for model endpoints.

A server that only speaks the older `initialize` handshake is refused; the
connector requires `server/discover`.

## Approving tools

Three files live in your repository, side by side:

| File | Format | Holds |
|---|---|---|
| `mcp-servers.toml` | `mcp-servers.v1` | The servers Forge may contact. A server not named here is never contacted. |
| `tool-approvals.json` | `tool-approvals.v1` | Which definition hash of which tool a person approved. |
| `tool-definitions/<sha256>.json` | `tool-definition.v1` | The approved definition itself, so a later change can be shown as a diff. |

Tools are defined in your repository; servers are only where they run.

```toml
schema_version = "mcp-servers.v1"
approvals = "tool-approvals.json"
definitions = "tool-definitions"

[[servers]]
server_id = "files"
transport = "streamable_http"
url = "http://127.0.0.1:8765/mcp"
```

### `forge mcp approve`

```bash
forge mcp status                                   # every tool and its state
forge mcp approve --approved-by alice --tool files/search
forge mcp approve --approved-by alice --all-new    # new, unflagged tools only
```

Before each decision the command shows what you are approving:

- a **new** tool: its full definition (description, input schema, output
  schema, annotations);
- a **changed** tool: a field-by-field diff of the definition you approved and
  the one the server now offers, from its `tool-definition.v1` snapshot (if no
  snapshot was recorded, it says so and shows the full new definition), plus
  any change of server origin;
- the **screening result**: the reasons for any flag, from the whole
  description (not just the 1 KiB a model sees) and the schemas.

The rules:

- A **flagged** tool needs `--accept-flagged`, named with `--tool`.
- A **changed or flagged** tool is confirmed by **typing its name**
  (`files__search`); `y` is not enough. Without a prompt (`--yes`) it needs
  `--expect-sha256` with the offered hash shown in its review, so a script
  cannot approve a definition nobody looked at.
- **`--all-new` never covers changed or flagged tools.** It lists them, each
  with the command to review it on its own.
- A new, unflagged tool takes `y` (or `--yes`).

Each approval writes the snapshot, updates `tool-approvals.json`, and is
recorded in the audit log as `agentforge.mcp.tool_approval` with the new and
previous hashes. Commit the approvals file and the snapshots together. A
snapshot is self-verifying: one edited by hand is refused, so it cannot make a
change look smaller than it is.

Server text is untrusted on your terminal too: control and bidirectional
characters are printed as visible escapes (such as `\u001b` and `\u202e`) and nothing is
rendered as markup.

The same is available from Python: `connector.approve("search",
approved_by="alice")` approves the definition offered **now** (flagged ones
need `accept_flagged=True`); add it with `approvals.with_approval(...)`,
record the definition with `DefinitionStore(...).save(found.definition())`,
and save both.

Each format has a JSON Schema in `forge/tools/mcp/` and examples in
`docs/examples/mcp/`. An approvals file:

```json
{
  "schema_version": "tool-approvals.v1",
  "approvals": [
    {
      "server_id": "files",
      "server_origin": "http://127.0.0.1:8765",
      "tool_name": "search",
      "definition_sha256": "0fd51c430bff67af9f809eb8d94565f0db9b3a2dc283b09c8120d2baeec31a35",
      "approved_by": "alice",
      "approved_at": "2026-11-26T09:00:00Z",
      "accepted_flags": []
    }
  ]
}
```

### The definition hash

`definition_sha256` is the SHA-256 of the
[RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) canonical JSON of
`{server_id, name, description, inputSchema, outputSchema, annotations}`
(absent members are `null`). Change any of those, even an annotation alone,
and the hash changes. `title`, `icons` and `_meta` are outside the hash;
nothing in them is shown to a model.

## What a tool can be

| State | Offered? | When |
|---|---|---|
| `approved` | yes | Server, origin, name and hash all match an approval. |
| `pending` | no | No approval exists. Default deny. |
| `changed` | no | An approval exists but the hash or origin differs. Emits `mcp.tool.definition_changed` with both hashes (never the description), also written to the audit log as `agentforge.mcp.tool_definition_changed`. Refused until re-approved. |
| `returned` | no | The tool disappeared from the server and came back during this connection. Reconnect to re-evaluate it. |
| `invalid` | no | Outside Forge's bounds: a schema over 64 KiB, nested deeper than 16 levels, with more than 128 properties, with a `$ref` outside itself or its own `$id`; a name a model API cannot carry; or two tools listed under one name. |

The connector re-lists and re-hashes on `refresh()`, and `watch()` follows the
server's `subscriptions/listen` stream and re-hashes on every tools change.
Neither ever approves anything.

## What Forge does not let a server do

- **Change a tool after approval.** The SDK's automatic "re-list and resend"
  on a `HEADER_MISMATCH` is not used; Forge re-hashes and refuses instead.
  The SDK's response cache is off, so a stale `tools/list` cannot hide a
  change.
- **Run a model, list roots, ask the user or stream logs.** Sampling, roots,
  elicitation and logging are not offered to servers (they are also deprecated
  in 2026-07-28). A tool that asks for input is refused.
- **Put text in a system message.** Descriptions are capped at 1 KiB with
  control and bidirectional-override characters stripped, and reach a model
  only as tool descriptions.
- **Send unchecked arguments.** Arguments are validated against the approved
  input schema before anything is sent.
- **Instruct the model through results.** Results are stripped, capped (16 KiB
  by default), screened, framed as untrusted data, and the `ToolResult` is
  marked `untrusted=True`. A flagged result emits `mcp.result.flagged` with
  the reasons only.
- **Leak through errors.** A server's error text never reaches a prompt,
  event, log or audit record; only the error class does.

## Policy

`McpConnector(connect_policy=..., call_policy=...)` take async callables that
return `True` to allow. They receive AgentPolicyPack-shaped requests:

```python
{
    "action": "mcp.server.connect",
    "resource": {
        "type": "McpServer",
        "server_id": "files",
        "transport": "streamable_http",
        "origin": "http://127.0.0.1:8765",
        "hosting": "local",
    },
}

{
    "action": "tool.call",
    "tool": {"name": "search", "namespace": "mcp.files", "definition_sha256": "..."},
    "arguments": {"query": "refunds"},
}
```

A denied connect never contacts the server.

## Authorization (OAuth)

For servers that require OAuth, pass a `ForgeOAuthProvider` as `auth=`. It is
the SDK's provider (RFC 9728 protected resource metadata, RFC 8707
`resource`, PKCE) with Forge's rules on top:

- **`iss` is always required** on the authorization response, whatever the
  authorization server advertises. A response without it is refused before
  the code is redeemed. A present `iss` must equal the authorization server's
  issuer exactly.
- **Client ID Metadata Documents only.** `client_metadata_url` is required.
  If the authorization server does not support CIMD, or a stored registration
  is anything else, the flow is refused. Forge never falls back to dynamic
  client registration and never sends a registration request.
- **Tokens** live in the `TokenStorage` you supply (for example a secret
  store), never in configuration, logs, events or exports.

!!! note "Issuer equivalence"
    When the SDK checks the issuer named in authorization server **metadata**,
    it treats a root issuer with and without its trailing slash
    (`https://auth.example` and `https://auth.example/`) as the same server
    (RFC 8414 section 3.3, legacy discovery path only). Nothing else is
    normalized. An authorization **response** `iss` gets no such allowance.
