"""``tool-definition.v1`` snapshots, and showing a person what they are approving.

An approval in ``tool-approvals.v1`` pins only a hash. To show *what changed*
when a server changes an approved tool, Forge also keeps the definition it
approved: one ``tool-definition.v1`` file per hash, in a directory next to the
approvals file, committed with it::

    tool-definitions/<definition_sha256>.json
    {"schema_version": "tool-definition.v1",
     "definition_sha256": "<64 hex>",
     "definition": {"server_id": ..., "name": ..., "description": ...,
                    "inputSchema": ..., "outputSchema": ..., "annotations": ...}}

A snapshot is self-verifying: the reader recomputes the hash and refuses a
file whose name, recorded hash and content disagree, so a tampered snapshot
cannot make a change look smaller than it is.

Everything a server wrote is untrusted, including on a terminal: an escape
sequence in a description could rewrite what the reviewer sees. Text is
passed through :func:`terminal_safe` before it is shown.

Standard library only.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from forge.tools.mcp.hashing import HASHED_FIELDS, canonical_json, definition_sha256

SCHEMA_VERSION: Final = "tool-definition.v1"
SCHEMA_PATH: Final = Path(__file__).with_name("tool-definition.schema.json")
MAX_FILE_BYTES: Final = 256 * 1024
SHOWN_FIELDS: Final = ("description", "inputSchema", "outputSchema", "annotations")

_HASH: Final = re.compile(r"[0-9a-f]{64}")
# C0 and C1 controls except tab and newline, DEL, line/paragraph separators and
# every bidirectional embedding, override and isolate.
_UNSAFE: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")


class DefinitionError(ValueError):
    """A definition snapshot is invalid or does not match its hash."""


def terminal_safe(text: str) -> str:
    """Make untrusted text safe to print: unsafe characters become visible escapes."""
    return _UNSAFE.sub(lambda m: f"\\u{ord(m.group()):04x}", text)


def _check_definition(definition: object) -> dict[str, Any]:
    if not isinstance(definition, dict) or tuple(sorted(definition)) != tuple(
        sorted(HASHED_FIELDS)
    ):
        raise DefinitionError(f"A definition must have exactly the keys {list(HASHED_FIELDS)}.")
    return definition


class DefinitionStore:
    """Definition snapshots, one file per definition hash."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, digest: str) -> Path:
        if not _HASH.fullmatch(digest):
            raise DefinitionError("A definition hash must be 64 lowercase hex characters.")
        return self.directory / f"{digest}.json"

    def save(self, definition: dict[str, Any]) -> str:
        """Record ``definition`` (if not already recorded) and return its hash."""
        definition = _check_definition(definition)
        digest = definition_sha256(
            definition["server_id"],
            definition["name"],
            description=definition["description"],
            input_schema=definition["inputSchema"],
            output_schema=definition["outputSchema"],
            annotations=definition["annotations"],
        )
        path = self.path(digest)
        if path.is_symlink():
            raise DefinitionError("A definition snapshot must not be a symbolic link.")
        if path.exists():
            self.load(digest)  # refuses a tampered existing file instead of trusting it
            return digest
        document = {
            "schema_version": SCHEMA_VERSION,
            "definition_sha256": digest,
            "definition": definition,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return digest

    def load(self, digest: str) -> dict[str, Any] | None:
        """The recorded definition for ``digest``, or ``None`` when none was recorded."""
        path = self.path(digest)
        if path.is_symlink():
            raise DefinitionError("A definition snapshot must not be a symbolic link.")
        if not path.exists():
            return None
        if path.stat().st_size > MAX_FILE_BYTES:
            raise DefinitionError("A definition snapshot is over 256 KiB.")
        try:
            document = json.loads(path.read_bytes().decode("utf-8"), parse_constant=_no_constants)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DefinitionError("A definition snapshot is not valid UTF-8 JSON.") from None
        if (
            not isinstance(document, dict)
            or set(document) != {"schema_version", "definition_sha256", "definition"}
            or document["schema_version"] != SCHEMA_VERSION
        ):
            raise DefinitionError(f"A definition snapshot is not {SCHEMA_VERSION}.")
        definition = _check_definition(document["definition"])
        recomputed = _sha(definition)
        if document["definition_sha256"] != digest or recomputed != digest:
            raise DefinitionError(
                "A definition snapshot does not match its hash; it was edited or corrupted."
            )
        return definition


def _no_constants(_literal: str) -> Any:
    raise DefinitionError("A definition snapshot contains NaN or Infinity.")


def _sha(definition: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(definition).encode("utf-8")).hexdigest()


def _lines(field: str, value: Any) -> list[str]:
    if value is None:
        return ["(none)"]
    if field == "description":
        return terminal_safe(str(value)).splitlines() or [""]
    return terminal_safe(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
    ).splitlines()


def render_definition(definition: dict[str, Any]) -> list[str]:
    """Every shown field of one definition, terminal-safe."""
    out: list[str] = []
    for field in SHOWN_FIELDS:
        out.append(f"{field}:")
        out.extend(f"    {line}" for line in _lines(field, definition.get(field)))
    return out


def render_diff(old: dict[str, Any] | None, new: dict[str, Any]) -> list[str]:
    """A field-by-field unified diff of the approved and offered definitions, terminal-safe."""
    if old is None:
        return [
            "The previously approved definition was not recorded (only its hash is known).",
            "Review the full definition now offered:",
            *render_definition(new),
        ]
    out: list[str] = []
    for field in SHOWN_FIELDS:
        before, after = _lines(field, old.get(field)), _lines(field, new.get(field))
        if before == after:
            out.append(f"{field}: unchanged")
            continue
        out.append(f"{field}: CHANGED")
        diff: Iterable[str] = difflib.unified_diff(
            before, after, fromfile=f"approved {field}", tofile=f"offered {field}", lineterm="", n=3
        )
        out.extend(f"    {line}" for line in diff)
    return out
