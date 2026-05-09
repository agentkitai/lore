"""Regression test: importance_score and confidence columns must stay dropped.

Phase 1 of the drop-quality-score-columns refactor removed two memory columns
(`importance_score` and `confidence`) and the corresponding
``lore.importance`` module. This test asserts that nothing reintroduces them
on a freshly-migrated SQLite database, and that the importance module stays
gone — both are easy to add back by accident in a future migration.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")

from lore.persistence.sqlite import SqliteStore


@pytest.mark.asyncio
async def test_memories_table_has_no_quality_score_columns(tmp_path):
    db = tmp_path / "lore.db"
    store = await SqliteStore.open(f"sqlite:///{db}")
    try:
        cur = await store._conn.execute("PRAGMA table_info(memories)")
        rows = await cur.fetchall()
        cols = [row[1] for row in rows]
        await cur.close()
        assert "importance_score" not in cols
        assert "confidence" not in cols
    finally:
        await store.close()


def test_importance_module_does_not_exist():
    with pytest.raises(ImportError):
        from lore import importance  # noqa: F401
