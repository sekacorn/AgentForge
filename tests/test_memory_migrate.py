"""The additive memory migration: privileges, concurrency, and `forge memory migrate`.

Offline: pgvector runs against fake asyncpg and pgvector modules, SQLite on a
temp file.
"""

from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from forge.cli.main import app
from forge.exceptions import MemoryDimensionError, MemoryMigrationError
from forge.memory.pgvector import PGVectorMemoryStore

PLANTED = "mig-planted-71af-do-not-log"
runner = CliRunner()


class FakePostgresError(Exception):
    """Stands in for asyncpg's error classes, which carry a SQLSTATE."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"permission denied for table forge_memories {PLANTED}")
        self.sqlstate = sqlstate


def _fake_backend(
    monkeypatch: pytest.MonkeyPatch,
    *,
    declared: str | None = "vector(64)",
    index_valid: bool | None = None,
    fail_on: str | None = None,
    sqlstate: str = "42501",
) -> tuple[MagicMock, AsyncMock]:
    conn = AsyncMock()

    async def fetchval(sql: str, *args: Any) -> Any:
        if "format_type" in sql:
            return declared
        if "indisvalid" in sql:
            return index_valid
        return None

    async def execute(sql: str, *args: Any) -> str:
        if fail_on is not None and fail_on in sql:
            raise FakePostgresError(sqlstate)
        return "OK"

    conn.fetchval = AsyncMock(side_effect=fetchval)
    conn.execute = AsyncMock(side_effect=execute)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    pool.close = AsyncMock()

    asyncpg = types.ModuleType("asyncpg")
    asyncpg.create_pool = AsyncMock(return_value=pool)  # type: ignore[attr-defined]
    pgvector_asyncpg = types.ModuleType("pgvector.asyncpg")
    pgvector_asyncpg.register_vector = AsyncMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg)
    monkeypatch.setitem(sys.modules, "pgvector", types.ModuleType("pgvector"))
    monkeypatch.setitem(sys.modules, "pgvector.asyncpg", pgvector_asyncpg)
    return pool, conn


def _executed(conn: AsyncMock) -> list[str]:
    return [str(call.args[0]) for call in conn.execute.call_args_list]


# --- the statements ----------------------------------------------------------------------


def test_every_pgvector_statement_is_idempotent_and_additive() -> None:
    statements = PGVectorMemoryStore("postgresql://memory-host/db").migration_sql()
    assert all("IF NOT EXISTS" in sql for sql in statements)
    assert not any(word in sql.upper() for sql in statements for word in ("DROP", "RENAME"))
    assert statements[-1].startswith(
        'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "forge_memories_item_id_idx"'
    )
    columns = [sql for sql in statements if "ADD COLUMN" in sql]
    assert [sql.split("IF NOT EXISTS ")[1] for sql in columns] == [
        "item_id TEXT",
        "status TEXT NOT NULL DEFAULT 'active'",
        "provenance JSONB",
    ]


async def test_opening_a_store_runs_the_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    _, conn = _fake_backend(monkeypatch)
    store = PGVectorMemoryStore("postgresql://memory-host/db")
    async with store:
        pass
    executed = _executed(conn)
    for statement in store.migration_sql():
        assert statement in executed


async def test_running_the_migration_twice_is_harmless(monkeypatch: pytest.MonkeyPatch) -> None:
    _, conn = _fake_backend(monkeypatch)
    for _ in range(2):
        async with PGVectorMemoryStore("postgresql://memory-host/db"):
            pass
    assert _executed(conn).count(PGVectorMemoryStore("postgresql://x").migration_sql()[0]) == 2


# --- failures are explained, never raw ----------------------------------------------------


@pytest.mark.parametrize("fail_on", ["ADD COLUMN IF NOT EXISTS item_id", "CREATE UNIQUE INDEX"])
async def test_missing_privileges_give_a_clear_instruction(
    monkeypatch: pytest.MonkeyPatch, fail_on: str
) -> None:
    pool, _ = _fake_backend(monkeypatch, fail_on=fail_on)
    store = PGVectorMemoryStore("postgresql://memory-host/db")
    with pytest.raises(MemoryMigrationError) as raised:
        async with store:
            pass
    message = str(raised.value)
    assert "lacks ALTER or CREATE privileges" in message
    assert "forge memory migrate --print-sql" in message
    assert PLANTED not in message and "memory-host" not in message
    assert raised.value.__cause__ is None
    pool.close.assert_awaited_once()


async def test_another_database_error_names_only_its_sqlstate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_backend(monkeypatch, fail_on="provenance", sqlstate="53100")
    with pytest.raises(MemoryMigrationError) as raised:
        async with PGVectorMemoryStore("postgresql://memory-host/db"):
            pass
    assert "SQLSTATE 53100" in str(raised.value)
    assert PLANTED not in str(raised.value)


async def test_an_invalid_index_from_an_interrupted_build_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, conn = _fake_backend(monkeypatch, index_valid=False)
    with pytest.raises(MemoryMigrationError) as raised:
        async with PGVectorMemoryStore("postgresql://memory-host/db"):
            pass
    assert "DROP INDEX CONCURRENTLY" in str(raised.value)
    assert not any("ALTER TABLE" in sql for sql in _executed(conn))


async def test_a_dimension_mismatch_is_refused_before_any_schema_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, conn = _fake_backend(monkeypatch, declared="vector(32)")
    with pytest.raises(MemoryDimensionError):
        async with PGVectorMemoryStore("postgresql://memory-host/db", embedding_dim=64):
            pass
    assert not any("ALTER TABLE" in sql for sql in _executed(conn))


# --- `forge memory migrate` ---------------------------------------------------------------


def _use(monkeypatch: pytest.MonkeyPatch, backend: str, path: Path | None = None) -> None:
    monkeypatch.setenv("FORGE_MEMORY_BACKEND", backend)
    if path is not None:
        monkeypatch.setenv("FORGE_MEMORY_PATH", str(path))
    if backend == "pgvector":
        monkeypatch.setenv("FORGE_PGVECTOR_DSN", "postgresql://forge@memory-host:5432/forge")


def test_print_sql_for_pgvector_connects_to_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "pgvector")
    monkeypatch.setitem(sys.modules, "asyncpg", None)  # any connection attempt would fail
    result = runner.invoke(app, ["memory", "migrate", "--print-sql"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert lines == [f"{sql};" for sql in PGVectorMemoryStore("postgresql://x").migration_sql()]
    assert "memory-host" not in result.output


def test_migrate_applies_the_sqlite_migration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "m.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE memories (id TEXT PRIMARY KEY, text TEXT NOT NULL, "
            "embedding TEXT NOT NULL, metadata TEXT, created_at TEXT NOT NULL)"
        )
    _use(monkeypatch, "sqlite", path)
    result = runner.invoke(app, ["memory", "migrate"])
    assert result.exit_code == 0, result.output
    assert "up to date" in result.stderr
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"status", "provenance"} <= columns
    assert {"memory_tombstones", "memory_rejected", "forge_memory_meta"} <= tables


def test_print_sql_for_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _use(monkeypatch, "sqlite", tmp_path / "m.db")
    result = runner.invoke(app, ["memory", "migrate", "--print-sql"])
    assert result.exit_code == 0, result.output
    assert "ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active';" in result.stdout
    assert not (tmp_path / "m.db").exists()  # printing never opens the store


def test_migrate_reports_a_refusal_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "m.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE forge_memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO forge_memory_meta VALUES ('dimensions', '512')")
    _use(monkeypatch, "sqlite", path)
    result = runner.invoke(app, ["memory", "migrate"])
    assert result.exit_code == 2
    assert "512-dimension" in result.output
    assert "Traceback" not in result.output


def test_an_in_memory_store_has_nothing_to_migrate(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "inmemory")
    result = runner.invoke(app, ["memory", "migrate"])
    assert result.exit_code == 0
    assert "nothing to migrate" in result.stderr
