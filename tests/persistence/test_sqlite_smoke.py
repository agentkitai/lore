"""Phase 3A smoke tests for SqliteStore — open/close/idempotency.

Per-method ops are stubbed in 3A and land in 3C–3F; here we just verify that
the foundation is live: the URL-scheme dispatch picks SqliteStore, the WAL
pragmas + sqlite-vec extension load, and the migration runner applies the
schema once and is idempotent.
"""

from __future__ import annotations

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
        # Phase 3D implemented all MemoryOps. Pick a still-stubbed slice
        # method (GraphOps lands in 3E+) to assert the stub surface
        # remains in place.
        with pytest.raises(NotImplementedError, match="get_entity"):
            await store.get_entity("ent_x")
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


# ── Phase 3B: vec0 virtual table + transaction helper ─────────────────


@pytest.mark.asyncio
async def test_memory_vectors_table_exists_after_open(tmp_path: Path):
    from lore.persistence.sqlite import SqliteStore

    store = await SqliteStore.open(f"sqlite:///{tmp_path / 'vec.db'}")
    try:
        async with store._acquire() as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='memory_vectors'"
            ) as cur:
                row = await cur.fetchone()
        assert row is not None, "memory_vectors vec0 virtual table should exist"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_vec0_knn_query_works(tmp_path: Path):
    from lore.persistence.sqlite import EMBED_DIM, SqliteStore

    store = await SqliteStore.open(f"sqlite:///{tmp_path / 'knn.db'}")
    try:
        # Two unit vectors pointing in different directions: identity-aligned
        # (1,0,...,0) and a vector with components on the first two axes.
        # Cosine distance to v1 is 0 for v1 itself and ~0.293 for v2.
        v1 = [0.0] * EMBED_DIM
        v1[0] = 1.0
        v2 = [0.0] * EMBED_DIM
        v2[0] = 0.7071
        v2[1] = 0.7071
        async with store.transaction() as tx:
            await tx.execute(
                "INSERT INTO memory_vectors(memory_rowid, embedding) VALUES (?, ?)",
                (1, repr(v1)),
            )
            await tx.execute(
                "INSERT INTO memory_vectors(memory_rowid, embedding) VALUES (?, ?)",
                (2, repr(v2)),
            )

        async with store._acquire() as conn:
            async with conn.execute(
                "SELECT memory_rowid, distance FROM memory_vectors "
                "WHERE embedding MATCH ? AND k = 2",
                (repr(v1),),
            ) as cur:
                rows = [dict(r) async for r in cur]

        assert len(rows) == 2
        assert rows[0]["memory_rowid"] == 1
        assert rows[0]["distance"] == pytest.approx(0.0, abs=1e-5)
        assert rows[1]["memory_rowid"] == 2
        assert rows[1]["distance"] > rows[0]["distance"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_transaction_rolls_back_on_exception(tmp_path: Path):
    from lore.persistence.sqlite import EMBED_DIM, SqliteStore

    store = await SqliteStore.open(f"sqlite:///{tmp_path / 'tx.db'}")
    try:
        v1 = [0.0] * EMBED_DIM
        v1[0] = 1.0
        v2 = [0.0] * EMBED_DIM
        v2[1] = 1.0
        async with store.transaction() as tx:
            await tx.execute(
                "INSERT INTO memory_vectors(memory_rowid, embedding) VALUES (?, ?)",
                (1, repr(v1)),
            )

        with pytest.raises(RuntimeError, match="forced"):
            async with store.transaction() as tx:
                await tx.execute(
                    "INSERT INTO memory_vectors(memory_rowid, embedding) VALUES (?, ?)",
                    (2, repr(v2)),
                )
                raise RuntimeError("forced")

        async with store._acquire() as conn:
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM memory_vectors"
            ) as cur:
                row = await cur.fetchone()
        assert row["n"] == 1, "Second insert should have rolled back"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_init_vec_tables_is_idempotent(tmp_path: Path):
    """Re-opening the same DB must not fail when memory_vectors already exists."""
    from lore.persistence.sqlite import SqliteStore

    db_path = tmp_path / "idempotent.db"
    store1 = await SqliteStore.open(f"sqlite:///{db_path}")
    await store1.close()

    store2 = await SqliteStore.open(f"sqlite:///{db_path}")
    try:
        async with store2._acquire() as conn:
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM memory_vectors"
            ) as cur:
                row = await cur.fetchone()
        assert row["n"] == 0
    finally:
        await store2.close()


@pytest.mark.asyncio
async def test_transaction_on_closed_store_raises(tmp_path: Path):
    from lore.persistence.sqlite import SqliteStore, StoreError

    store = await SqliteStore.open(f"sqlite:///{tmp_path / 'closed.db'}")
    await store.close()

    with pytest.raises(StoreError, match="closed"):
        async with store.transaction():
            pass
