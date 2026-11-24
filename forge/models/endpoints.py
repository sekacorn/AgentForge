"""Classifying a model endpoint as local or hosted, and describing it safely.

Two questions are answered here and nowhere else, so they cannot be answered
differently in two places: does this ``base_url`` point at this machine or a
private network, and what part of it is safe to put in an event or a log.

"Local" is deliberately narrow: loopback, the unspecified address, link-local,
or a private range. A hostname is never resolved, because resolution is a
network call and a name that resolves privately today may not tomorrow. Anything
that cannot be positively established as local is treated as hosted, since the
cost of being wrong in that direction is a refused or flagged run rather than
content leaving the machine unannounced.

The same rule exists in ModelSwapBench. The two projects take no dependency on
each other, so it is restated here and pinned by tests on both sides.
"""

from __future__ import annotations

import ipaddress
from typing import Final
from urllib.parse import urlsplit

LOCAL: Final = "local"
HOSTED: Final = "hosted"
UNKNOWN: Final = "unknown"

#: 0.0.0.0 is accepted because development servers bind it to mean "this host".
LOCAL_HOSTNAMES: Final = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})  # nosec B104


def endpoint_is_local(base_url: str) -> bool:
    """Whether ``base_url`` points at this machine or a private network."""
    try:
        host = urlsplit(base_url).hostname or ""
    except ValueError:
        return False
    if host in LOCAL_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_private or address.is_loopback or address.is_link_local)


def hosting_for(base_url: str) -> str:
    """``"local"`` or ``"hosted"`` for an endpoint, derived and never declared."""
    return LOCAL if endpoint_is_local(base_url) else HOSTED


def endpoint_origin(base_url: str) -> str:
    """The scheme and host of ``base_url``, with everything else removed.

    Credentials, path, query and fragment are dropped, because any of them can
    carry a secret or a tenant identifier and this value is meant for events
    and logs. An unparseable URL yields ``"unknown"`` rather than being echoed.
    """
    try:
        parts = urlsplit(base_url)
        port = parts.port
    except ValueError:
        return UNKNOWN
    if not parts.scheme or not parts.hostname:
        return UNKNOWN
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")
