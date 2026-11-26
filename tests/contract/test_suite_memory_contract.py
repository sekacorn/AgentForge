"""Every memory export and import report Forge writes must be readable by the suite.

Like the inventory contract beside it: the contract between AgentForge and the
AI Infrastructure Suite is the document (``agent-memory.v1``,
``agent-memory-import.v1``, and the linked ``embedding-portability.v1``), whose
schemas the suite owns. Skips without the suite installed, unless
``AGENTFORGE_REQUIRE_SUITE_CONTRACT=1`` (set by the suite-contract job).
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from forge.memory.conversation import ConversationMemory
from forge.memory.transfer import export_conversation, export_memory, import_memory
from forge.memory.vector import InMemoryVectorStore
from forge.types import Message, Role, ToolCall, ToolResult

REQUIRE_ENV = "AGENTFORGE_REQUIRE_SUITE_CONTRACT"
NOW = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)


def _import_or_skip(module: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError:
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail(f"{module} is not installed and {REQUIRE_ENV}=1.")
        pytest.skip(f"{module} is not installed.", allow_module_level=True)


agent_memory = _import_or_skip("ai_infrastructure_suite.agent_memory")
memory_vector = _import_or_skip("ai_infrastructure_suite.memory_vector")
embedding_portability = _import_or_skip("ai_infrastructure_suite.embedding_portability")
jsonschema = _import_or_skip("jsonschema")


def _schema(name: str) -> Any:
    return json.loads(files("ai_infrastructure_suite").joinpath(f"data/{name}").read_text("utf-8"))


async def _store() -> InMemoryVectorStore:
    store = InMemoryVectorStore(dim=16)
    await store.add("Damaged items are replaced within thirty days.", metadata={"topic": "returns"})
    await store.add("Invoices are issued on the first business day.")
    await store.add("Support is available in English and Spanish.")
    gone = await store.add("A note that was later erased.")
    await store.delete_record(gone, reason="erasure request")
    return store


def _export(tmp_path: Path, **options: Any) -> Path:
    async def run() -> Any:
        return await export_memory(
            await _store(),
            tmp_path / "export",
            store_id="kb-memory",
            backend="in_memory",
            export_id="exp-contract",
            now=NOW,
            shard_items=2,
            **options,
        )

    return asyncio.run(run()).path


@pytest.mark.parametrize("options", [{}, {"redact_pii": True}, {"include_embeddings": False}])
def test_every_export_variant_passes_the_suites_schema_and_reader(
    tmp_path: Path, options: dict[str, Any]
) -> None:
    path = _export(tmp_path, **options)
    document = json.loads(path.read_text("utf-8"))
    validator = jsonschema.Draft202012Validator(_schema("agent-memory.schema.json"))
    assert [error.message for error in validator.iter_errors(document)] == []
    item_validator = jsonschema.Draft202012Validator(
        {"$ref": "#/$defs/item", "$defs": _schema("agent-memory.schema.json")["$defs"]}
    )
    for shard in (path.parent / "items").glob("*.jsonl"):
        for line in shard.read_text("utf-8").splitlines():
            assert list(item_validator.iter_errors(json.loads(line))) == []
    export = agent_memory.load_agent_memory(
        path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )
    store = export.store("kb-memory")
    assert (store.active, store.deleted_by_policy) == (3, 1)


def test_the_linked_embedding_inventory_is_valid_and_bound(tmp_path: Path) -> None:
    path = _export(tmp_path)
    link = agent_memory.load_agent_memory(path).store("kb-memory").embeddings
    inventory = path.parent / link.inventory_path
    scan = embedding_portability.scan_embedding_portability(
        inventory, evidence_sha256=link.inventory_sha256
    )
    assert [s.store_id for s in scan.stores] == ["kb-memory"]


def test_a_conversation_export_passes_the_suites_reader(tmp_path: Path) -> None:
    memory = ConversationMemory()
    memory.add(Message(role=Role.SYSTEM, content="You are helpful."))
    memory.add(Message(role=Role.USER, content="What time is it?"))
    memory.add(Message(role=Role.ASSISTANT, tool_calls=[ToolCall(id="call-1", name="time")]))
    memory.add(
        Message(
            role=Role.TOOL,
            name="time",
            tool_results=[ToolResult(tool_call_id="call-1", name="time", content="10:00")],
        )
    )
    summary = export_conversation(memory, tmp_path / "conv", store_id="session", now=NOW)
    store = agent_memory.load_agent_memory(summary.path).store("session")
    assert (store.kind, store.active) == ("conversation", 4)


def test_an_import_report_binds_and_scores_25(tmp_path: Path) -> None:
    path = _export(tmp_path)

    async def run() -> Any:
        return await import_memory(
            InMemoryVectorStore(dim=16),
            path.parent,
            report_path=tmp_path / "import-report.json",
            destination_backend="in_memory",
            destination_store_id="kb-memory",
            now=NOW,
        )

    summary = asyncio.run(run())
    report = agent_memory.load_memory_import_report(summary.report_path)
    assert report.source_export_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    entry = {
        "state": "memory_inventory",
        "path": "export/agent-memory.json",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "import_report": {
            "path": "import-report.json",
            "sha256": hashlib.sha256(summary.report_path.read_bytes()).hexdigest(),
        },
    }
    result = memory_vector.build_vector(tmp_path, entry)
    assert result.row["score"] == 25
    assert result.section[0]["embeddings"]["items_without_vector"] == 0
