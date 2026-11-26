"""Export memory as ``agent-memory.v1`` and import it back, verifiably.

The AI Infrastructure Suite owns the format's JSON Schema and scores exports
for the ``memory`` lock-in vector; this module writes and reads the documents
and never imports the suite. The suite's ``suite-contract`` job feeds what this
module writes to the suite's own readers.

**Export** writes, into an empty directory::

    agent-memory.json                       the document (digests of everything below)
    items/0000.jsonl ...                    one item per line, canonical bytes
    embeddings/embedding-portability.json   the vectors, for a store that has them
    embeddings/vectors/0000.jsonl ...
    embeddings/sources/<sha256>.txt         each item's exact text, once per text

Vectors are recomputed with the store's own deterministic scheme over exactly
the exported text, so every vector is bound to its item by the item's
``content_sha256``. Deleted items appear only as tombstones (ID and time). An
item that still looks like it holds a credential stops the export and the
error names the item ID and the kind of secret, never the value. PII redaction
(``redact_pii=True``) is applied first and recorded in the document.

**Import** reads an export with a bounded reader that re-checks every digest,
brings its items into a :class:`~forge.memory.portable.PortableMemory` store
(where they land quarantined), applies its tombstones, and writes an
``agent-memory-import.v1`` report bound to the export's digest.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from forge._version import __version__
from forge.compliance.redaction import PIIRedactor
from forge.exceptions import MemoryBackendError
from forge.memory.conversation import ConversationMemory
from forge.memory.portable import (
    HASHED_BOW_SCHEME,
    ImportSource,
    MemoryRecord,
    PortableMemory,
    Tombstone,
)
from forge.memory.vector import _stable_bucket, _tokenize
from forge.security.secrets import secret_kinds_in
from forge.types import utcnow

MEMORY_SCHEMA_VERSION: Final = "agent-memory.v1"
IMPORT_SCHEMA_VERSION: Final = "agent-memory-import.v1"
EMBEDDING_SCHEMA_VERSION: Final = "embedding-portability.v1"
DOCUMENT_NAME: Final = "agent-memory.json"
MAX_DOCUMENT_BYTES: Final = 4 * 1024 * 1024
MAX_SHARD_BYTES: Final = 16 * 1024 * 1024
MAX_LINE_BYTES: Final = 64 * 1024
MAX_TEXT_BYTES: Final = 48 * 1024
MAX_ITEMS: Final = 100_000
DEFAULT_SHARD_ITEMS: Final = 1000

_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_ITEM_KEYS: Final = (
    "item_id",
    "kind",
    "text",
    "content_sha256",
    "metadata",
    "created_at",
    "status",
    "provenance",
)


class MemoryTransferError(MemoryBackendError):
    """An export or import was refused. The message never repeats item text."""


@dataclass(frozen=True)
class ExportSummary:
    """What an export wrote."""

    path: Path
    sha256: str
    export_id: str
    items: int
    quarantined: int
    tombstones: int
    embeddings: bool


@dataclass(frozen=True)
class ImportSummary:
    """What an import did, and where its report went."""

    report_path: Path
    report_sha256: str
    received: int
    imported: int
    unchanged: int
    refused: dict[str, str]
    tombstones_applied: int


# --- canonical writing ---------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _timestamp(value: datetime) -> str:
    value = value.astimezone(UTC)
    text = value.strftime("%Y-%m-%dT%H:%M:%S")
    if value.microsecond:
        text += f".{value.microsecond:06d}"
    return text + "Z"


def _line(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _write_json(path: Path, document: object) -> str:
    data = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return _sha(data)


def _empty_target(out_dir: Path) -> None:
    if out_dir.is_symlink() or (out_dir.exists() and any(out_dir.iterdir())):
        raise MemoryTransferError("Export into an empty directory, not an existing one or a link.")
    out_dir.mkdir(parents=True, exist_ok=True)


def _identifier(value: str, what: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise MemoryTransferError(
            f"A {what} is not a portable identifier ([A-Za-z0-9][A-Za-z0-9_.:-]{{0,127}})."
        )
    return value


def _embed(text: str, dimensions: int) -> list[float]:
    """The bundled stores' hashed bag-of-words embedding, reproduced exactly."""
    vector = [0.0] * dimensions
    for token in _tokenize(text):
        vector[_stable_bucket(token, dimensions)] += 1.0
    norm = math.sqrt(sum(component * component for component in vector))
    return [component / norm for component in vector] if norm > 0 else vector


def _shards(
    lines: Sequence[bytes], directory: Path, prefix: str, size: int
) -> list[dict[str, Any]]:
    shards: list[dict[str, Any]] = []
    for index in range(0, len(lines), size):
        chunk = lines[index : index + size]
        data = b"".join(chunk)
        name = f"{prefix}/{index // size:04d}.jsonl"
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        shards.append({"path": name, "sha256": _sha(data), "record_count": len(chunk)})
    return shards


def _refuse_secrets(item_id: str, *values: Any) -> None:
    kinds = secret_kinds_in(*values)
    if kinds:
        raise MemoryTransferError(
            f"Item {item_id} appears to hold a credential ({', '.join(kinds)}); redact or "
            "delete it, then export again. Nothing was written."
        )


# --- export ----------------------------------------------------------------------------


def _document_item(record: MemoryRecord, redactor: PIIRedactor | None) -> dict[str, Any]:
    text = redactor.redact(record.text) if redactor else record.text
    metadata = redactor.redact_obj(record.metadata) if redactor else record.metadata
    _refuse_secrets(record.item_id, text, metadata)
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise MemoryTransferError(f"Item {record.item_id} is over 48 KiB; chunk it before export.")
    provenance = None
    if record.provenance is not None:
        provenance = {
            "source_system": record.provenance.source_system,
            "source_backend": record.provenance.source_backend,
            "source_store_id": record.provenance.source_store_id,
            "export_id": record.provenance.export_id,
            "export_sha256": record.provenance.export_sha256,
            "original_item_id": record.provenance.original_item_id,
            "imported_at": _timestamp(record.provenance.imported_at),
            "importer": {
                "tool": record.provenance.importer_tool,
                "version": record.provenance.importer_version,
            },
        }
    return {
        "item_id": _identifier(record.item_id, "memory item ID"),
        "kind": "document",
        "text": text,
        "content_sha256": _sha(text.encode("utf-8")),
        "metadata": metadata,
        "created_at": None if record.created_at is None else _timestamp(record.created_at),
        "status": record.status,
        "provenance": provenance,
    }


def _embedding_inventory(
    items: Sequence[dict[str, Any]], directory: Path, store_id: str, dimensions: int, size: int
) -> str:
    """Write the linked embedding-portability.v1 document; returns its digest."""
    root = directory / "embeddings"
    vector_lines: list[bytes] = []
    sources: list[dict[str, Any]] = []
    for item in items:
        digest = item["content_sha256"]
        source = root / "sources" / f"{digest}.txt"
        if not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(item["text"].encode("utf-8"))
        vector_lines.append(
            _line(
                {
                    "record_id": item["item_id"],
                    "source_text_sha256": digest,
                    "values": [round(v, 8) for v in _embed(item["text"], dimensions)],
                }
            )
        )
        sources.append(
            {
                "record_id": item["item_id"],
                "source_text": {
                    "status": "retained",
                    "path": f"sources/{digest}.txt",
                    "sha256": digest,
                },
            }
        )
    document = {
        "schema_version": EMBEDDING_SCHEMA_VERSION,
        "stores": [
            {
                "store_id": store_id,
                "embedding_model": {
                    "id": HASHED_BOW_SCHEME,
                    "revision": "1",
                    "dimensions": dimensions,
                    "availability": "open_weight_self_hostable",
                    "availability_verification": "attested",
                },
                "vectors": {
                    "layout": "sharded_jsonl",
                    "shards": _shards(vector_lines, root, "vectors", size),
                    "source_text": sources,
                },
            }
        ],
    }
    return _write_json(root / "embedding-portability.json", document)


def _document(
    *,
    export_id: str,
    redaction: str | None,
    system: str,
    backend: str,
    store: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "generated_by": {"tool": "agentforge-oss", "version": __version__},
        "export": {
            "export_id": export_id,
            "created_at": _timestamp(now),
            "redaction_profile": redaction,
            "source": {"system": system, "backend": backend},
        },
        "stores": [store],
    }


async def export_memory(
    store: PortableMemory,
    out_dir: Path,
    *,
    store_id: str,
    backend: str,
    source_system: str = "agentforge",
    export_id: str | None = None,
    redact_pii: bool = False,
    include_embeddings: bool = True,
    shard_items: int = DEFAULT_SHARD_ITEMS,
    now: datetime | None = None,
) -> ExportSummary:
    """Export a portable store as ``agent-memory.v1`` into an empty directory."""
    now = now or utcnow()
    export_id = _identifier(export_id or f"exp-{now.strftime('%Y%m%dT%H%M%SZ')}", "export ID")
    _identifier(store_id, "store ID")
    _identifier(source_system, "source system")
    _identifier(backend, "backend name")
    if shard_items < 1:
        raise MemoryTransferError("shard_items must be at least 1.")
    redactor = PIIRedactor() if redact_pii else None

    records: list[MemoryRecord] = []
    after: str | None = None
    while True:
        page = await store.list_records(after=after, limit=1000)
        records.extend(page.records)
        if len(records) > MAX_ITEMS:
            raise MemoryTransferError("The store holds more than 100,000 items.")
        if page.next_after is None:
            break
        after = page.next_after
    # Every item is checked (and refused) before anything is written.
    items = [_document_item(record, redactor) for record in records]
    tombstones = [
        {"item_id": _identifier(t.item_id, "tombstoned ID"), "deleted_at": _timestamp(t.deleted_at)}
        for t in await store.list_tombstones()
    ]

    _empty_target(out_dir)
    embeddings = None
    if include_embeddings and items:
        scheme = store.embedding_scheme
        inventory_sha = _embedding_inventory(
            items, out_dir, store_id, scheme.dimensions, shard_items
        )
        embeddings = {
            "inventory": {"path": "embeddings/embedding-portability.json", "sha256": inventory_sha},
            "inventory_store_id": store_id,
        }
    layout: dict[str, Any]
    if items:
        layout = {
            "layout": "sharded_jsonl",
            "shards": _shards([_line(item) for item in items], out_dir, "items", shard_items),
        }
    else:
        layout = {"layout": "single_file", "records": []}
    quarantined = sum(1 for item in items if item["status"] == "quarantined")
    document = _document(
        export_id=export_id,
        redaction="pii" if redact_pii else None,
        system=source_system,
        backend=backend,
        store={
            "store_id": store_id,
            "kind": "retrieval",
            "embeddings": embeddings,
            "items": layout,
            "counts": {
                "active": len(items) - quarantined,
                "quarantined": quarantined,
                "deleted_by_policy": len(tombstones),
            },
            "tombstones": tombstones,
        },
        now=now,
    )
    path = out_dir / DOCUMENT_NAME
    return ExportSummary(
        path=path,
        sha256=_write_json(path, document),
        export_id=export_id,
        items=len(items),
        quarantined=quarantined,
        tombstones=len(tombstones),
        embeddings=embeddings is not None,
    )


def export_conversation(
    memory: ConversationMemory,
    out_dir: Path,
    *,
    store_id: str,
    source_system: str = "agentforge",
    export_id: str | None = None,
    redact_pii: bool = False,
    now: datetime | None = None,
) -> ExportSummary:
    """Export a conversation transcript as an ``agent-memory.v1`` conversation store.

    Messages carry no IDs or times of their own, so items are numbered
    ``msg-000000``, ``msg-000001``, ... and ``created_at`` is null.
    """
    now = now or utcnow()
    export_id = _identifier(export_id or f"exp-{now.strftime('%Y%m%dT%H%M%SZ')}", "export ID")
    _identifier(store_id, "store ID")
    redactor = PIIRedactor() if redact_pii else None
    records: list[dict[str, Any]] = []
    for sequence, message in enumerate(memory.history()):
        item_id = f"msg-{sequence:06d}"
        text = redactor.redact(message.content) if redactor else message.content
        calls = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ]
        results = [
            {
                "tool_call_id": result.tool_call_id,
                "name": result.name,
                "content": redactor.redact(result.content) if redactor else result.content,
                "is_error": result.is_error,
            }
            for result in message.tool_results
        ]
        metadata = redactor.redact_obj(message.metadata) if redactor else message.metadata
        if redactor:
            calls = redactor.redact_obj(calls)
        _refuse_secrets(item_id, text, metadata, calls, results)
        records.append(
            {
                "item_id": item_id,
                "kind": "message",
                "sequence": sequence,
                "role": message.role.value,
                "name": message.name,
                "text": text,
                "tool_calls": calls,
                "tool_results": results,
                "content_sha256": _sha(text.encode("utf-8")),
                "metadata": metadata,
                "created_at": None,
                "status": "active",
                "provenance": None,
            }
        )
    _empty_target(out_dir)
    document = _document(
        export_id=export_id,
        redaction="pii" if redact_pii else None,
        system=_identifier(source_system, "source system"),
        backend="conversation_buffer",
        store={
            "store_id": store_id,
            "kind": "conversation",
            "embeddings": None,
            "items": {"layout": "single_file", "records": records},
            "counts": {"active": len(records), "quarantined": 0, "deleted_by_policy": 0},
            "tombstones": [],
        },
        now=now,
    )
    path = out_dir / DOCUMENT_NAME
    return ExportSummary(
        path=path,
        sha256=_write_json(path, document),
        export_id=export_id,
        items=len(records),
        quarantined=0,
        tombstones=0,
        embeddings=False,
    )


# --- reading an export (bounded, independent of the suite) ------------------------------------


@dataclass(frozen=True)
class _ParsedStore:
    store_id: str
    kind: str
    records: list[MemoryRecord]
    tombstones: list[Tombstone]


@dataclass(frozen=True)
class _ParsedExport:
    sha256: str
    export_id: str
    system: str
    backend: str
    stores: list[_ParsedStore]


def _read(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise MemoryTransferError("An export file is missing, linked, or not a regular file.")
    data = path.read_bytes()
    if len(data) > limit:
        raise MemoryTransferError("An export file is over its size limit.")
    return data


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MemoryTransferError("An export repeats a JSON key.")
        result[key] = value
    return result


def _reject_constant(_literal: str) -> Any:
    raise MemoryTransferError("An export holds NaN or Infinity.")


def _load(data: bytes) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MemoryTransferError("An export file is not well-formed UTF-8 JSON.") from None


def _relative(root: Path, name: object) -> Path:
    if (
        not isinstance(name, str)
        or not name
        or name.startswith(("/", "\\"))
        or ".." in name.split("/")
    ):
        raise MemoryTransferError("An export names a path outside itself.")
    return root / name


def _record(item: Any, kind: str) -> MemoryRecord | None:
    if not isinstance(item, dict) or item.get("kind") != (
        "document" if kind == "retrieval" else "message"
    ):
        raise MemoryTransferError("An export item does not match its store's kind.")
    text = item.get("text")
    if not isinstance(text, str) or _sha(text.encode("utf-8")) != item.get("content_sha256"):
        raise MemoryTransferError("An export item's text differs from its content_sha256.")
    if kind != "retrieval":
        return None
    item_id = item.get("item_id")
    if not isinstance(item_id, str) or not _IDENTIFIER.fullmatch(item_id):
        raise MemoryTransferError("An export item has an invalid ID.")
    created = item.get("created_at")
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        raise MemoryTransferError("An export item's metadata is not an object.")
    return MemoryRecord(
        item_id=item_id,
        text=text,
        metadata=metadata,
        created_at=None if created is None else _when(created, "item time"),
    )


def _object(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MemoryTransferError(f"An export's {what} is not an object.")
    return value


def _id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise MemoryTransferError(f"An export's {what} is not a portable identifier.")
    return value


def _when(value: Any, what: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise MemoryTransferError(f"An export's {what} is not a valid time.") from None


def read_export(path: Path) -> _ParsedExport:
    """Read and check an ``agent-memory.v1`` export: every digest is re-verified."""
    path = path / DOCUMENT_NAME if path.is_dir() else path
    data = _read(path, MAX_DOCUMENT_BYTES)
    document = _load(data)
    if not isinstance(document, dict) or document.get("schema_version") != MEMORY_SCHEMA_VERSION:
        raise MemoryTransferError(f"Expected an {MEMORY_SCHEMA_VERSION} document.")
    root = path.parent
    export = _object(document.get("export"), "export block")
    source = _object(export.get("source"), "source block")
    raw_stores = document.get("stores")
    if not isinstance(raw_stores, list) or not raw_stores:
        raise MemoryTransferError("An export lists no stores.")
    stores: list[_ParsedStore] = []
    for raw_store in raw_stores:
        store = _object(raw_store, "store")
        kind = store.get("kind")
        if kind not in ("retrieval", "conversation"):
            raise MemoryTransferError("An export store has an unknown kind.")
        items_block = _object(store.get("items"), "items block")
        raw: Iterable[Any]
        if items_block.get("layout") == "single_file":
            raw = items_block.get("records") or []
        elif items_block.get("layout") == "sharded_jsonl":
            lines: list[Any] = []
            for shard in items_block.get("shards") or []:
                shard_data = _read(_relative(root, shard.get("path")), MAX_SHARD_BYTES)
                if _sha(shard_data) != shard.get("sha256"):
                    raise MemoryTransferError("An export shard differs from its declared SHA-256.")
                for line in shard_data.splitlines():
                    if len(line) > MAX_LINE_BYTES:
                        raise MemoryTransferError("An export shard line is over the size limit.")
                    if line.strip():
                        lines.append(_load(line))
            raw = lines
        else:
            raise MemoryTransferError("An export store has an unknown items layout.")
        records = [record for item in raw if (record := _record(item, kind)) is not None]
        tombstones: list[Tombstone] = []
        for raw_tombstone in store.get("tombstones") or []:
            entry = _object(raw_tombstone, "tombstone")
            if set(entry) != {"item_id", "deleted_at"}:
                raise MemoryTransferError("An export tombstone carries more than an ID and a time.")
            tombstones.append(
                Tombstone(
                    item_id=_id(entry["item_id"], "tombstone ID"),
                    deleted_at=_when(entry["deleted_at"], "tombstone time"),
                )
            )
        stores.append(
            _ParsedStore(
                store_id=_id(store.get("store_id"), "store ID"),
                kind=kind,
                records=records,
                tombstones=tombstones,
            )
        )
    return _ParsedExport(
        sha256=_sha(data),
        export_id=_id(export.get("export_id"), "export ID"),
        system=_id(source.get("system"), "source system"),
        backend=_id(source.get("backend"), "source backend"),
        stores=stores,
    )


# --- import ----------------------------------------------------------------------------


async def import_memory(
    store: PortableMemory,
    export_path: Path,
    *,
    report_path: Path,
    destination_backend: str,
    destination_store_id: str,
    source_store_id: str | None = None,
    destination_system: str = "agentforge",
    now: datetime | None = None,
) -> ImportSummary:
    """Import one retrieval store of an export, then write the import report.

    Items land quarantined; the export's tombstones are applied first, so a
    deleted item cannot arrive by the same import.
    """
    now = now or utcnow()
    export = read_export(export_path)
    candidates = [s for s in export.stores if source_store_id in (None, s.store_id)]
    if len(candidates) != 1:
        raise MemoryTransferError(
            "Name the source store to import with source_store_id (the export holds "
            f"{len(export.stores)})."
        )
    source = candidates[0]
    if source.kind != "retrieval":
        raise MemoryTransferError("Only retrieval stores can be imported into a memory backend.")
    for tombstone in source.tombstones:
        await store.delete_record(
            tombstone.item_id, reason=f"tombstone from export {export.export_id}"
        )
    report = await store.import_records(
        source.records,
        source=ImportSource(
            source_system=export.system,
            source_backend=export.backend,
            source_store_id=source.store_id,
            export_id=export.export_id,
            export_sha256=export.sha256,
            imported_at=now,
            importer_tool="agentforge-oss",
            importer_version=__version__,
        ),
    )
    document = {
        "schema_version": IMPORT_SCHEMA_VERSION,
        "generated_by": {"tool": "agentforge-oss", "version": __version__},
        "import": {
            "import_id": f"imp-{now.strftime('%Y%m%dT%H%M%SZ')}",
            "imported_at": _timestamp(now),
            "destination": {
                "system": _identifier(destination_system, "destination system"),
                "backend": _identifier(destination_backend, "backend name"),
            },
        },
        "source_export": {"export_id": export.export_id, "sha256": export.sha256},
        "stores": [
            {
                "source_store_id": source.store_id,
                "destination_store_id": _identifier(destination_store_id, "store ID"),
                "received": report.received,
                "imported": report.imported,
                "unchanged": report.unchanged,
                "refused": report.refused_count,
                "tombstones_applied": len(source.tombstones),
            }
        ],
    }
    return ImportSummary(
        report_path=report_path,
        report_sha256=_write_json(report_path, document),
        received=report.received,
        imported=report.imported,
        unchanged=report.unchanged,
        refused=dict(report.refused),
        tombstones_applied=len(source.tombstones),
    )
