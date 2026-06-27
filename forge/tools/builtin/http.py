"""An HTTP GET tool (network egress — marked dangerous).

This tool is a good illustration of Forge's security posture: it is genuinely
useful but performs network I/O, so it is flagged ``dangerous=True``. The
security layer denies dangerous tools unless they are explicitly allowed, which
keeps egress off by default for untrusted workloads.
"""

from __future__ import annotations

import httpx

from forge.tools.base import tool

_MAX_BYTES = 20_000


@tool(dangerous=True)
async def http_get(url: str, timeout: float = 10.0) -> str:  # noqa: ASYNC109
    """Fetch a URL with an HTTP GET request and return the response body (truncated).

    Args:
        url: The absolute http(s) URL to fetch.
        timeout: Request timeout in seconds.
    """
    if not url.lower().startswith(("http://", "https://")):
        return "Error: only http(s) URLs are supported."
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        return f"Error: request failed ({exc})"

    body = response.text[:_MAX_BYTES]
    suffix = "... [truncated]" if len(response.text) > _MAX_BYTES else ""
    return f"HTTP {response.status_code}\n{body}{suffix}"
