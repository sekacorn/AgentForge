"""``tool-approvals.v1``: which MCP tool definitions a person has approved.

The file lives in the user's Git repository, next to the configuration that
names the servers: tools are *defined* in the repo, servers are only where
they run. Each approval pins one tool on one server to one definition hash::

    {"schema_version": "tool-approvals.v1",
     "approvals": [{"server_id": "files", "server_origin": "http://127.0.0.1:8765",
                    "tool_name": "search", "definition_sha256": "<64 hex>",
                    "approved_by": "alice", "approved_at": "2026-11-26T09:00:00Z",
                    "accepted_flags": []}]}

A tool is **approved** only when its server, its server's origin, its name and
its current definition hash all match an entry. Anything else is refused.
``accepted_flags`` records the screening reasons a person accepted when they
approved a flagged definition; an empty list means nothing was flagged.

The reader is bounded and strict: unknown keys, duplicate keys, NaN, a
duplicate (server, tool) pair or a malformed hash all reject the whole file.
Standard library only. The JSON Schema ships beside this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from forge.tools.mcp.bounds import SERVER_ID

SCHEMA_VERSION: Final = "tool-approvals.v1"
SCHEMA_PATH: Final = Path(__file__).with_name("tool-approvals.schema.json")
MAX_FILE_BYTES: Final = 1024 * 1024
MAX_APPROVALS: Final = 4096

_HASH: Final = re.compile(r"[0-9a-f]{64}")
_TOOL_NAME: Final = re.compile(r"[A-Za-z0-9_.\-/]{1,128}")
_PERSON: Final = re.compile(r"[^\x00-\x1f\x7f]{1,128}")
_FLAG: Final = re.compile(r"[a-z0-9_]{1,64}")
_TIMESTAMP: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_ENTRY_KEYS: Final = frozenset(
    {
        "server_id",
        "server_origin",
        "tool_name",
        "definition_sha256",
        "approved_by",
        "approved_at",
        "accepted_flags",
    }
)


class ApprovalsError(ValueError):
    """An approvals file is invalid. The message never repeats file content."""


@dataclass(frozen=True)
class ToolApproval:
    """One approved tool definition."""

    server_id: str
    server_origin: str | None
    tool_name: str
    definition_sha256: str
    approved_by: str
    approved_at: str
    accepted_flags: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "server_origin": self.server_origin,
            "tool_name": self.tool_name,
            "definition_sha256": self.definition_sha256,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "accepted_flags": list(self.accepted_flags),
        }


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ApprovalsError("The approvals file has a duplicate key.")
        result[key] = value
    return result


def _reject_constant(_literal: str) -> Any:
    raise ApprovalsError("The approvals file contains NaN or Infinity.")


def _entry(raw: object, index: int) -> ToolApproval:
    if not isinstance(raw, dict) or set(raw) != _ENTRY_KEYS:
        raise ApprovalsError(f"Approval {index} must have exactly the keys {sorted(_ENTRY_KEYS)}.")
    server_id, origin, tool_name = raw["server_id"], raw["server_origin"], raw["tool_name"]
    digest, approver, when, flags = (
        raw["definition_sha256"],
        raw["approved_by"],
        raw["approved_at"],
        raw["accepted_flags"],
    )
    if not isinstance(server_id, str) or not SERVER_ID.fullmatch(server_id):
        raise ApprovalsError(f"Approval {index} has an invalid server_id.")
    if origin is not None and (not isinstance(origin, str) or len(origin) > 256 or "@" in origin):
        raise ApprovalsError(f"Approval {index} has an invalid server_origin.")
    if isinstance(origin, str) and not re.fullmatch(r"https?://[^/?#\s]+", origin):
        raise ApprovalsError(
            f"Approval {index} server_origin must be an origin (scheme://host[:port])."
        )
    if not isinstance(tool_name, str) or not _TOOL_NAME.fullmatch(tool_name):
        raise ApprovalsError(f"Approval {index} has an invalid tool_name.")
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise ApprovalsError(f"Approval {index} has an invalid definition_sha256.")
    if not isinstance(approver, str) or not _PERSON.fullmatch(approver):
        raise ApprovalsError(f"Approval {index} has an invalid approved_by.")
    if not isinstance(when, str) or not _TIMESTAMP.fullmatch(when):
        raise ApprovalsError(f"Approval {index} approved_at must be UTC, YYYY-MM-DDTHH:MM:SSZ.")
    if (
        not isinstance(flags, list)
        or len(flags) > 16
        or not all(isinstance(flag, str) and _FLAG.fullmatch(flag) for flag in flags)
        or len(set(flags)) != len(flags)
    ):
        raise ApprovalsError(f"Approval {index} has invalid accepted_flags.")
    return ToolApproval(server_id, origin, tool_name, digest, approver, when, tuple(flags))


class ToolApprovals:
    """An immutable set of approvals, keyed by (server ID, tool name)."""

    def __init__(self, approvals: Iterable[ToolApproval] = ()) -> None:
        self._by_key: dict[tuple[str, str], ToolApproval] = {}
        for approval in approvals:
            key = (approval.server_id, approval.tool_name)
            if key in self._by_key:
                raise ApprovalsError("Two approvals name the same server and tool.")
            self._by_key[key] = approval
        if len(self._by_key) > MAX_APPROVALS:
            raise ApprovalsError(f"More than {MAX_APPROVALS} approvals.")

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self) -> Iterator[ToolApproval]:
        return iter(sorted(self._by_key.values(), key=lambda a: (a.server_id, a.tool_name)))

    def get(self, server_id: str, tool_name: str) -> ToolApproval | None:
        return self._by_key.get((server_id, tool_name))

    def is_approved(self, server_id: str, origin: str | None, tool_name: str, digest: str) -> bool:
        approval = self.get(server_id, tool_name)
        return (
            approval is not None
            and approval.definition_sha256 == digest
            and approval.server_origin == origin
        )

    def with_approval(self, approval: ToolApproval) -> ToolApprovals:
        """A copy with ``approval`` added, replacing any earlier one for the same tool."""
        kept = [
            a
            for a in self
            if (a.server_id, a.tool_name) != (approval.server_id, approval.tool_name)
        ]
        return ToolApprovals([*kept, approval])

    def to_json(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "approvals": [a.to_json() for a in self]}

    @classmethod
    def parse(cls, data: bytes) -> ToolApprovals:
        if len(data) > MAX_FILE_BYTES:
            raise ApprovalsError("The approvals file is over 1 MiB.")
        try:
            document = json.loads(
                data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApprovalsError("The approvals file is not valid UTF-8 JSON.") from None
        if not isinstance(document, dict) or set(document) != {"schema_version", "approvals"}:
            raise ApprovalsError(
                "The approvals file must have exactly schema_version and approvals."
            )
        if document["schema_version"] != SCHEMA_VERSION:
            raise ApprovalsError(f"The approvals file is not {SCHEMA_VERSION}.")
        entries = document["approvals"]
        if not isinstance(entries, list) or len(entries) > MAX_APPROVALS:
            raise ApprovalsError("approvals must be a list of at most 4096 entries.")
        return cls(_entry(raw, index) for index, raw in enumerate(entries))

    @classmethod
    def load(cls, path: Path) -> ToolApprovals:
        """Read an approvals file. A missing file means nothing is approved."""
        if path.is_symlink():
            raise ApprovalsError("The approvals file must not be a symbolic link.")
        if not path.exists():
            return cls()
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ApprovalsError("The approvals file is over 1 MiB.")
        return cls.parse(path.read_bytes())

    def save(self, path: Path) -> None:
        """Write the file deterministically (sorted, two-space indent, trailing newline)."""
        if path.is_symlink():
            raise ApprovalsError("The approvals file must not be a symbolic link.")
        data = json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(data, encoding="utf-8", newline="\n")
        temporary.replace(path)


def new_approval(
    *,
    server_id: str,
    server_origin: str | None,
    tool_name: str,
    definition_sha256: str,
    approved_by: str,
    accepted_flags: Iterable[str] = (),
    now: datetime | None = None,
) -> ToolApproval:
    """Build a validated approval stamped with the current UTC time."""
    when = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    approval = ToolApproval(
        server_id,
        server_origin,
        tool_name,
        definition_sha256,
        approved_by,
        when,
        tuple(sorted(set(accepted_flags))),
    )
    _entry(approval.to_json(), 0)
    return approval
