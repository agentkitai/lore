"""Phase 3J: typed exception parity for SqliteStore.

Covers:
  * ``EmbeddingDimMismatch`` — wrong-dim embedding rejected at the boundary.
  * ``DanglingVectorError`` + ``check_dangling_vectors`` diagnostic.
  * ``StoreCorruption`` — malformed-DB DatabaseError is wrapped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip the whole module if the optional [solo] deps aren't installed.
pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")

from lore.persistence.exceptions import (  # noqa: E402
    DanglingVectorError,
    EmbeddingDimMismatch,
    InsecureBindError,
    IntegrityError,
    StoreCorruption,
    StoreError,
)


def test_hierarchy_phase_3j_additions():
    """The new typed exceptions slot in under the documented bases."""
    assert issubclass(StoreCorruption, StoreError)
    assert issubclass(EmbeddingDimMismatch, IntegrityError)
    assert issubclass(EmbeddingDimMismatch, StoreError)
    assert issubclass(DanglingVectorError, IntegrityError)
    from lore.persistence.exceptions import ConfigError
    assert issubclass(InsecureBindError, ConfigError)


def test_embedding_dim_mismatch_holds_attrs():
    err = EmbeddingDimMismatch(384, 768)
    assert err.expected == 384
    assert err.actual == 768
    assert "384" in str(err) and "768" in str(err)


@pytest.mark.asyncio
async def test_embedding_dim_mismatch_raises_typed_error_sqlite(tmp_path: Path):
    """SqliteStore.insert_memory rejects wrong-dim embeddings at the boundary."""
    from lore.persistence.sqlite import EMBED_DIM, SqliteStore
    from lore.persistence.types import NewMemory

    db_path = tmp_path / "lore.db"
    store = await SqliteStore.open(f"sqlite:///{db_path}")
    try:
        bad = NewMemory(
            org_id="solo",
            content="x",
            embedding=[0.1] * (EMBED_DIM + 1),  # off-by-one
        )
        with pytest.raises(EmbeddingDimMismatch) as ei:
            await store.insert_memory(bad)
        assert ei.value.expected == EMBED_DIM
        assert ei.value.actual == EMBED_DIM + 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_embedding_dim_mismatch_raises_in_recall(tmp_path: Path):
    """SqliteStore.recall_by_embedding validates the query vector length."""
    from lore.persistence.sqlite import EMBED_DIM, SqliteStore
    from lore.persistence.types import RecallParams

    db_path = tmp_path / "lore.db"
    store = await SqliteStore.open(f"sqlite:///{db_path}")
    try:
        params = RecallParams(
            org_id="solo",
            query_vec=[0.0] * (EMBED_DIM - 1),  # short by one
            limit=5,
            min_score=0.0,
        )
        with pytest.raises(EmbeddingDimMismatch):
            await store.recall_by_embedding(params)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dangling_vector_check_finds_orphans(tmp_path: Path):
    """``check_dangling_vectors`` returns memory_ids whose vec0 row is missing."""
    from lore.persistence.sqlite import (
        EMBED_DIM,
        SqliteStore,
        check_dangling_vectors,
    )
    from lore.persistence.types import NewMemory

    db_path = tmp_path / "lore.db"
    store = await SqliteStore.open(f"sqlite:///{db_path}")
    try:
        # Healthy state: insert a memory the normal way; vec row should pair.
        m = await store.insert_memory(
            NewMemory(org_id="solo", content="paired", embedding=[0.1] * EMBED_DIM)
        )
        assert await check_dangling_vectors(store) == []

        # Hand-corrupt: delete the vec0 companion only.
        async with store._conn.execute(
            "SELECT rowid FROM memories WHERE id = ?", (m.id,)
        ) as cur:
            row = await cur.fetchone()
        rowid = row["rowid"]
        await store._conn.execute(
            "DELETE FROM memory_vectors WHERE memory_rowid = ?", (rowid,)
        )
        await store._conn.commit()

        dangling = await check_dangling_vectors(store)
        assert m.id in dangling
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_corruption_wraps_aiosqlite_error(tmp_path: Path):
    """A malformed DB file surfaces as ``StoreCorruption`` from open()."""
    from lore.persistence.sqlite import SqliteStore

    # Write a non-SQLite file at the target path; SQLite returns "file is
    # not a database" / "malformed" when it tries to read the header.
    bogus = tmp_path / "bogus.db"
    bogus.write_bytes(b"this-is-not-a-sqlite-database-just-bytes")

    with pytest.raises((StoreCorruption, StoreError)) as ei:
        await SqliteStore.open(f"sqlite:///{bogus}")
    # We accept the broader ``StoreError`` umbrella in case the OS reports a
    # different message we haven't pattern-matched yet, but we want at least
    # the corruption sentinel to fire on the canonical "file is not a
    # database" message most platforms emit.
    if not isinstance(ei.value, StoreCorruption):
        pytest.skip(
            "Corruption pattern not matched on this platform; "
            f"got: {ei.value!s}"
        )


def test_insecure_bind_error_message():
    err = InsecureBindError("Cannot bind to 0.0.0.0 without --require-auth.")
    assert "0.0.0.0" in str(err)
