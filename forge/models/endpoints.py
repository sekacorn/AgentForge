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
import logging
import re
from collections.abc import Iterable, Mapping
from typing import Final
from urllib.parse import urlsplit

LOCAL: Final = "local"
HOSTED: Final = "hosted"
UNKNOWN: Final = "unknown"

#: 0.0.0.0 is accepted because development servers bind it to mean "this host".
LOCAL_HOSTNAMES: Final = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})  # nosec B104


#: Documentation (RFC 5737, RFC 3849) and benchmarking (RFC 2544) ranges.
#: Python's ``is_private`` includes them, but they are neither this machine nor
#: a private network, so an endpoint there is hosted. The same five ranges are
#: excluded in AgentForge, ModelSwapBench and the AI Infrastructure Suite, and
#: the shared ``endpoint-locality-cases.v1`` table pins all three.
NOT_LOCAL_NETWORKS: Final = tuple(
    ipaddress.ip_network(network)
    for network in (
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "198.18.0.0/15",
        "2001:db8::/32",
    )
)


def _in_not_local_network(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether ``address``, or the IPv4 address it maps, is in a range above."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in NOT_LOCAL_NETWORKS)


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
    if _in_not_local_network(address):
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


#: Raised verbatim, so the fix is in the message and the URL is not.
URL_CREDENTIALS_MESSAGE = (
    "A configured URL must not contain credentials (user:password@host). "
    "Remove them from the URL and supply the key through api_key or its "
    "environment variable."
)
URL_FRAGMENT_MESSAGE = "A configured URL must not contain a fragment (#...)."
URL_QUERY_MESSAGE = (
    "A configured URL must not contain a query string. Put parameters the "
    "endpoint requires in default_query, whose values are redacted in records "
    "and logs."
)

#: Bounds on ``default_query``: a few named parameters such as ``api-version``.
MAX_DEFAULT_QUERY_PARAMS: Final = 16
MAX_DEFAULT_QUERY_VALUE_CHARS: Final = 512
_QUERY_KEY = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def url_has_credentials(base_url: str) -> bool:
    """Whether ``base_url`` carries a username or password.

    Such a URL is refused rather than used: HTTP clients log the request URL,
    so credentials placed there reach the logs on every call. A URL that cannot
    be parsed is judged by whether its authority contains ``@``.
    """
    try:
        parts = urlsplit(base_url)
        return parts.username is not None or parts.password is not None
    except ValueError:
        authority = base_url.split("//", 1)[-1].split("/", 1)[0]
        return "@" in authority


def url_problem(url: str, *, allow_query: bool = False) -> str | None:
    """Why a configured URL is refused, as a fixed message, or ``None``.

    Credentials and fragments are always refused. A query string is refused
    unless ``allow_query``; parameters an endpoint needs go in
    ``default_query`` instead, where their values are kept out of records and
    logs. The message never contains any part of the URL.
    """
    if url_has_credentials(url):
        return URL_CREDENTIALS_MESSAGE
    if "#" in url:
        return URL_FRAGMENT_MESSAGE
    if not allow_query and "?" in url:
        return URL_QUERY_MESSAGE
    return None


def check_configured_url(url: str, *, allow_query: bool = False) -> None:
    """Raise :class:`ConfigurationError` if ``url`` fails :func:`url_problem`."""
    problem = url_problem(url, allow_query=allow_query)
    if problem is not None:
        from forge.exceptions import ConfigurationError

        raise ConfigurationError(problem, context={"endpoint": endpoint_origin(url)})


def validate_default_query(default_query: Mapping[str, str] | None) -> dict[str, str]:
    """Return a checked copy of ``default_query``. Errors never repeat a value."""
    from forge.exceptions import ConfigurationError

    if not default_query:
        return {}
    if len(default_query) > MAX_DEFAULT_QUERY_PARAMS:
        raise ConfigurationError("default_query has too many parameters.")
    checked: dict[str, str] = {}
    for key, value in default_query.items():
        if not isinstance(key, str) or not _QUERY_KEY.fullmatch(key):
            raise ConfigurationError("default_query has an invalid parameter name.")
        if not isinstance(value, str) or not value or len(value) > MAX_DEFAULT_QUERY_VALUE_CHARS:
            raise ConfigurationError(
                "default_query has an invalid value.", context={"parameter": key}
            )
        checked[key] = value
    return checked


# --- keeping default_query values out of logs ---------------------------------

#: Loggers that may print a request URL or request options. The HTTP clients
#: log each request URL at INFO; the vendor SDKs log request options at DEBUG.
_LOGGERS_THAT_PRINT_URLS: Final = (
    "httpx",
    "httpcore",
    "openai",
    "openai._base_client",
    "anthropic",
    "anthropic._base_client",
)
_QUERY_VALUE = re.compile(r"([?&][^=&\s\"'#]+=)[^&\s\"'#]+")
_REDACTED: Final = "[redacted]"
_SECRET_QUERY_VALUES: set[str] = set()


class _RedactQueryValues(logging.Filter):
    """Replace query-string values, and any registered value, in a log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging over redaction
            return True
        redacted = _QUERY_VALUE.sub(lambda m: m.group(1) + _REDACTED, message)
        for value in sorted(_SECRET_QUERY_VALUES, key=len, reverse=True):
            redacted = redacted.replace(value, _REDACTED)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


_FILTER = _RedactQueryValues()


def redact_query_values_in_logs(values: Iterable[str] = ()) -> None:
    """Keep query values, and these values in particular, out of client logs.

    Idempotent. Installed whenever a provider is given a ``default_query``.
    """
    _SECRET_QUERY_VALUES.update(value for value in values if value)
    for name in _LOGGERS_THAT_PRINT_URLS:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
