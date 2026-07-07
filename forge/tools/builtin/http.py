"""An HTTP GET tool (network egress — marked dangerous).

This tool is a good illustration of Forge's security posture: it is genuinely
useful but performs network I/O, so it is flagged ``dangerous=True``. The
security layer denies dangerous tools unless they are explicitly allowed, which
keeps egress off by default for untrusted workloads.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from forge.tools.base import tool

_MAX_BYTES = 20_000

#: Hosts refused outright to blunt the most dangerous SSRF targets (cloud metadata
#: and loopback). This is a coarse literal denylist, not full SSRF protection: it
#: does not defeat DNS rebinding, alternate IP encodings, or other private ranges —
#: operators allowlisting this tool should still enforce network egress controls.
_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        # Blocked host literal, not a bind address.
        "0.0.0.0",  # nosec B104
        "::1",
        "169.254.169.254",
        "metadata.google.internal",
    }
)


@tool(dangerous=True)
async def http_get(url: str, timeout: float = 10.0) -> str:  # noqa: ASYNC109
    """Fetch a URL with an HTTP GET request and return the response body (truncated).

    Args:
        url: The absolute http(s) URL to fetch.
        timeout: Request timeout in seconds.
    """
    if not url.lower().startswith(("http://", "https://")):
        return "Error: only http(s) URLs are supported."
    if (urlparse(url).hostname or "").lower() in _BLOCKED_HOSTS:
        return "Error: refusing to fetch a loopback or link-local address."

    try:
        # Redirects are not followed (a redirect to an internal address is a classic
        # SSRF vector), and the body is streamed so a huge response cannot exhaust
        # memory before the size cap applies.
        async with (
            httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client,
            client.stream("GET", url) as response,
        ):
            status = response.status_code
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_BYTES:
                    break
    except httpx.HTTPError as exc:
        return f"Error: request failed ({exc})"

    body = b"".join(chunks)[:_MAX_BYTES].decode("utf-8", errors="replace")
    suffix = "... [truncated]" if total > _MAX_BYTES else ""
    return f"HTTP {status}\n{body}{suffix}"
