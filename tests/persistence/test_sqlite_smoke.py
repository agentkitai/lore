"""Phase 3A smoke tests for SqliteStore — open/close/idempotency.

Per-method ops are stubbed in 3A and land in 3C–3F; here we just verify that
the foundation is live: the URL-scheme dispatch picks SqliteStore, the WAL
pragmas + sqlite-vec extension load, and the migration runner applies the
schema once and is idempotent.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

# Skip the whole module if the optional [solo] deps aren't installed.
pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")


@pytest.mark.asyncio
async def test_open_empty_db_runs_migrations(tmp_path: Path):
    from lore.persistence.sqlite import SqliteStore

    db_path = tmp_path / "lore.db"
    store = await SqliteStore.open(f"sqlite:///{db_path}")
    try:
        async with store._acquire() as conn:
            async with conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ) as cur:
                versions = [row["version"] async for row in cur]
    finally:
        await store.close()

    # Expect at least one migration applied (specific list comes from 3A T3
    # — when the migration files exist, this set should match the Postgres tree).
    assert versions, "schema_migrations should be populated after open()"
    assert all(v.isdigit() and len(v) == 3 for v in versions), versions


@pytest.mark.asyncio
async def test_open_is_idempotent(tmp_path: Path):
    from lore.persistence.sqlite import SqliteStore

    db_path = tmp_path / "lore.db"
    store1 = await SqliteStore.open(f"sqlite:///{db_path}")
    async with store1._acquire() as conn:
        async with conn.execute(
            "SELECT COUNT(*) AS n FROM schema_migrations"
        ) as cur:
            row = await cur.fetchone()
            count_first = row["n"]
    await store1.close()

    # Second open — should not re-apply any migration.
    store2 = await SqliteStore.open(f"sqlite:///{db_path}")
    async with store2._acquire() as conn:
        async with conn.execute(
            "SELECT COUNT(*) AS n FROM schema_migrations"
        ) as cur:
            row = await cur.fetchone()
            count_second = row["n"]
    await store2.close()

    assert count_first == count_second


@pytest.mark.asyncio
async def test_make_store_dispatches_sqlite_url(tmp_path: Path):
    from lore.persistence import make_store
    from lore.persistence.sqlite import SqliteStore

    store = await make_store(f"sqlite:///{tmp_path / 'factory.db'}")
    try:
        assert isinstance(store, SqliteStore)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_method_stubs_raise_not_implemented(tmp_path: Path):
    from lore.persistence.sqlite import SqliteStore

    store = await SqliteStore.open(f"sqlite:///{tmp_path / 'stubs.db'}")
    try:
        with pytest.raises(NotImplementedError, match="get_memory"):
            await store.get_memory("mem_x", org_id="org-x")
    finally:
        await store.close()


def test_resolve_db_path_in_memory():
    from lore.persistence.sqlite import _resolve_db_path

    assert _resolve_db_path("sqlite:///:memory:") == ":memory:"


def test_resolve_db_path_relative():
    from lore.persistence.sqlite import _resolve_db_path

    assert _resolve_db_path("sqlite:///tmp/lore.db") == "tmp/lore.db"


def test_resolve_db_path_absolute():
    from lore.persistence.sqlite import _resolve_db_path

    assert _resolve_db_path("sqlite:////var/lib/lore.db") == "/var/lib/lore.db"


def test_resolve_db_path_home_expansion():
    from lore.persistence.sqlite import _resolve_db_path

    result = _resolve_db_path("sqlite:///~/.lore/lore.db")
    assert "~" not in result
    assert result.endswith(".lore/lore.db")
