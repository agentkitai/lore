"""Phase 6G T1: ``memories.scope`` column on the SQLite store.

Tested behaviour:
- A memory inserted with the default ``scope`` reads back as ``"project"``.
- A memory inserted with explicit ``scope="global"`` reads back as ``"global"``.
- The migration's type-based backfill flips ``meta.type`` ∈
  {lesson, preference, pattern, convention} rows to ``scope='global'``.

The Postgres mirror migration is shape-identical and exercised by the
existing contract suite (which now selects/returns the column).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from lore.persistence import NewMemory


def _vec(seed: int) -> Sequence[float]:
    """Deterministic 384-dim vector (matches the contract-test helper)."""
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


@pytest.fixture
def _sqlite_url(tmp_path: Path) -> str:
    db = tmp_path / "scope.db"
    return f"sqlite:///{db}"


@pytest.mark.asyncio
async def test_default_scope_is_project(_sqlite_url: str):
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.factory import make_store

    store = await make_store(_sqlite_url)
    try:
        # FK target: tests/persistence/conftest.py seeds canonical orgs for
        # contract tests; here we wire the same row directly.
        await store._conn.execute(
            "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
            ("solo", "Solo Test"),
        )
        await store._conn.commit()

        nm = NewMemory(
            org_id="solo",
            content="default-scope memory",
            embedding=_vec(1),
            project="lore",
            meta={"type": "note"},
        )
        stored = await store.insert_memory(nm)
        assert stored.scope == "project"

        fetched = await store.get_memory("solo", stored.id)
        assert fetched is not None
        assert fetched.scope == "project"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_explicit_global_scope_round_trip(_sqlite_url: str):
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.factory import make_store

    store = await make_store(_sqlite_url)
    try:
        await store._conn.execute(
            "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
            ("solo", "Solo Test"),
        )
        await store._conn.commit()

        nm = NewMemory(
            org_id="solo",
            content="universal lesson",
            embedding=_vec(2),
            scope="global",
            meta={"type": "lesson"},
        )
        stored = await store.insert_memory(nm)
        assert stored.scope == "global"

        fetched = await store.get_memory("solo", stored.id)
        assert fetched is not None
        assert fetched.scope == "global"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_backfill_flips_universal_types_to_global(_sqlite_url: str):
    """Phase 6G migration: type-based backfill UPDATE.

    The migration ships ``UPDATE memories SET scope = 'global' WHERE
    json_extract(meta, '$.type') IN ('lesson', 'preference', 'pattern',
    'convention')``. Easier to test by inserting rows with default scope
    via insert_memory, then re-running the backfill SQL ourselves and
    verifying the read-back.
    """
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.factory import make_store

    store = await make_store(_sqlite_url)
    try:
        await store._conn.execute(
            "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
            ("solo", "Solo Test"),
        )
        await store._conn.commit()

        # Insert one of each type that should flip + a control row.
        ids: dict[str, str] = {}
        for kind, label in [
            ("lesson", "lesson row"),
            ("preference", "pref row"),
            ("pattern", "pattern row"),
            ("convention", "convention row"),
            ("note", "note row (control)"),
        ]:
            stored = await store.insert_memory(
                NewMemory(
                    org_id="solo",
                    content=label,
                    embedding=_vec(hash(kind) & 0xFF),
                    meta={"type": kind},
                )
            )
            ids[kind] = stored.id
            assert stored.scope == "project"  # default before backfill

        # Re-apply the migration's backfill UPDATE.
        await store._conn.execute(
            """
            UPDATE memories
            SET scope = 'global'
            WHERE json_extract(meta, '$.type') IN
                  ('lesson', 'preference', 'pattern', 'convention')
            """
        )
        await store._conn.commit()

        for kind in ("lesson", "preference", "pattern", "convention"):
            fetched = await store.get_memory("solo", ids[kind])
            assert fetched is not None
            assert fetched.scope == "global", (
                f"type={kind!r} should backfill to global"
            )
        # Control: 'note' stays 'project'.
        note = await store.get_memory("solo", ids["note"])
        assert note is not None
        assert note.scope == "project"
    finally:
        await store.close()
