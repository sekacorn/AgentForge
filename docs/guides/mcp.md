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

Approvals live in a `tool-approvals.v1` file in your repository, next to the
configuration that names your servers. Tools are defined in your repository;
servers are only where they run.

```python
async with McpConnector(files, ToolApprovals()) as connector:
    for found in connector.discovered():
        print(found.name, found.state, found.reason)
    approval = connector.approve("search", approved_by="alice")
    approvals = approvals.with_approval(approval)
    approvals.save(Path("tool-approvals.json"))  # then review and commit it
```

`approve` approves the definition the server is offering **now**. Its
description and schema are screened for prompt-injection indicators first. A
flagged definition is refused unless you pass `accept_flagged=True` after
reading it; the reasons you accepted are recorded in the approval as
`accepted_flags`.

The file is plain JSON with a JSON Schema at
`forge/tools/mcp/tool-approvals.schema.json` and examples in
`docs/examples/mcp/`:

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
