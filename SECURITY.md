# Security policy

## Supported versions

AgentForge (`agentforge-oss`) is Beta software. Security fixes are applied to the
latest `0.5.x` release only. Upgrade with `pip install -U agentforge-oss`.

## Published advisories

| Advisory | Affected | Fixed in |
|---|---|---|
| [GHSA-r9m2-584h-q5c5](https://github.com/sekacorn/AgentForge/security/advisories/GHSA-r9m2-584h-q5c5): credentials could leak into exception messages, tracebacks and logs through provider errors and request URLs | `<= 0.5.5` | `0.5.6` |

All advisories: [github.com/sekacorn/AgentForge/security/advisories](https://github.com/sekacorn/AgentForge/security/advisories).

## Reporting a vulnerability

Please do not report a suspected vulnerability in a public issue.

Use **Report a vulnerability** on the repository's
[Security tab](https://github.com/sekacorn/AgentForge/security) when it is
available. If it is not, open an issue titled "Security contact request" that
contains **no details**, and the maintainer will arrange a private channel.

Include the affected version, reproduction steps and the impact you observed.
Never include real credentials: a placeholder such as `sk-...` is enough to show
where one appeared.

## Security properties

- **Offline by default.** The test suite runs with offline provider-shaped fakes
  and no API keys; no test contacts a network service.
- **Credentials stay out of URLs, logs and records.** Configured URLs refuse
  credentials, query strings and fragments; required parameters go in
  `default_query` and are redacted. Provider errors surface their class, not
  their text.
- **Dangerous tools are denied by default** in the sandbox, and an MCP server
  built with `forge mcp serve` exposes nothing unless named.
- **Untrusted content stays untrusted.** Imported memory is quarantined; MCP
  tool definitions are approved by hash and their results are marked untrusted.
- **No PyPI API token.** Releases use Trusted Publishing (OIDC), are gated on the
  `pypi` environment, and wait for a maintainer's approval.
