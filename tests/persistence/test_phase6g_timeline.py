"""Phase 6G T6 — ``Store.list_timeline_around`` direct persistence tests.

Drives the SQLite implementation. The Postgres mirror has the same shape
and is exercised through the route-level tests via the ``store`` fixture
in production code paths.

Tested behaviour:
* Returns ``(anchor, [])`` when the anchor is missing or the org_id
  doesn't match (404-equivalent).
* Same-project + ±max_hours window filtering.
* Direction split for ``both``: ceil(limit/2) before + floor(limit/2)
  after, ASC overall.
* ``before`` and ``after`` direction modes.
* Cross-project rows are excluded.
* The anchor itself is excluded from the adjacent list.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import pytest

from lore.persistence import NewMemory


def _vec(seed: int) -> Sequence[float]:
    """Deterministic 384-dim vector — embedding content is irrelevant here."""
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


@pytest.fixture
def _sqlite_url(tmp_path: Path) -> str:
    db = tmp_path / "timeline.db"
    return f"sqlite:///{db}"


def _ts(base: datetime, *, minutes: int = 0, hours: int = 0) -> str:
    """Format a datetime offset as the SQLite-native TEXT shape."""
    t = base + timedelta(minutes=minutes, hours=hours)
    return t.strftime("%Y-%m-%d %H:%M:%S")


async def _insert_with_ts(
    store,
    *,
    org_id: str,
    project: str,
    content: str,
    created_at_ts: str,
    session_id: str | None = None,
    seed: int = 0,
    type_: str = "observation",
):
    """Insert a memory then overwrite ``created_at`` to the chosen time.

    The DB-side DEFAULT writes ``datetime('now')``; we update afterwards
    so we can position rows precisely on the timeline axis.
    """
    meta = {"type": type_}
    if session_id is not None:
        meta["session_id"] = session_id
    nm = NewMemory(
        org_id=org_id,
        content=content,
        embedding=_vec(seed),
        project=project,
        meta=meta,
    )
    stored = await store.insert_memory(nm)
    await store._conn.execute(
        "UPDATE memories SET created_at = ? WHERE id = ?",
        (created_at_ts, stored.id),
    )
    await store._conn.commit()
    return stored


@pytest.mark.asyncio
async def test_timeline_around_returns_adjacent_in_same_project(_sqlite_url: str):
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.factory import make_store

    store = await make_store(_sqlite_url)
    try:
        await store._conn.execute(
            "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
            ("solo", "Solo"),
        )
        await store._conn.commit()
        base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
        rows = []
        for i, off in enumerate([-30, -15, 0, 15, 30]):  # minutes around base
            rows.append(
                await _insert_with_ts(
                    store,
                    org_id="solo",
                    project="lore",
                    content=f"event-{i}",
                    created_at_ts=_ts(base, minutes=off),
                    session_id="sess-1",
                    seed=i,
                )
            )
        anchor = rows[2]  # the middle row at offset 0

        anchor_out, adjacent = await store.list_timeline_around(
            anchor_id=anchor.id,
            org_id="solo",
            direction="both",
            limit=4,
            max_hours=2.0,
        )
        assert anchor_out is not None
        assert anchor_out.id == anchor.id
        # 4 entries: 2 before + 2 after, sorted ASC.
        ids = [m.id for m in adjacent]
        assert ids == [rows[0].id, rows[1].id, rows[3].id, rows[4].id]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_timeline_around_excludes_other_project(_sqlite_url: str):
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.factory import make_store

    store = await make_store(_sqlite_url)
    try:
        await store._conn.execute(
            "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
            ("solo", "Solo"),
        )
        await store._conn.commit()
        base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
        anchor = await _insert_with_ts(
            store,
            org_id="solo",
            project="p",
            content="anchor",
            created_at_ts=_ts(base),
            seed=0,
        )
        # Same time, different project.
        await _insert_with_ts(
            store,
            org_id="solo",
            project="q",
            content="other-project",
            created_at_ts=_ts(base, minutes=10),
            seed=1,
        )
        # Same project sibling.
        sibling = await _insert_with_ts(
            store,
            org_id="solo",
            project="p",
            content="same-project sibling",
            created_at_ts=_ts(base, minutes=20),
            seed=2,
        )

        anchor_out, adjacent = await store.list_timeline_around(
            anchor_id=anchor.id,
            org_id="solo",
            direction="both",
            limit=10,
            max_hours=2.0,
        )
        assert anchor_out is not None
        ids = [m.id for m in adjacent]
        assert ids == [sibling.id]  # cross-project row not included
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_timeline_around_respects_max_hours(_sqlite_url: str):
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.factory import make_store

    store = await make_store(_sqlite_url)
    try:
        await store._conn.execute(
            "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
            ("solo", "Solo"),
        )
        await store._conn.commit()
        base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
        anchor = await _insert_with_ts(
            store,
            org_id="solo",
            project="p",
            content="anchor",
            created_at_ts=_ts(base),
            seed=0,
        )
        await _insert_with_ts(
            store,
            org_id="solo",
            project="p",
            content="five-hours-out",
            created_at_ts=_ts(base, hours=5),
            seed=1,
        )

        anchor_out, adjacent = await store.list_timeline_around(
            anchor_id=anchor.id,
            org_id="solo",
            direction="both",
            limit=10,
            max_hours=2.0,
        )
        assert anchor_out is not None
        assert adjacent == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_timeline_around_direction_before(_sqlite_url: str):
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.factory import make_store

    store = await make_store(_sqlite_url)
    try:
        await store._conn.execute(
            "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
            ("solo", "Solo"),
        )
        await store._conn.commit()
        base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
        rows = []
        for i, off in enumerate([-30, -15, 0, 15, 30]):
            rows.append(
                await _insert_with_ts(
                    store,
                    org_id="solo",
                    project="p",
                    content=f"event-{i}",
                    created_at_ts=_ts(base, minutes=off),
                    seed=i,
                )
            )
        anchor = rows[2]

        anchor_out, adjacent = await store.list_timeline_around(
            anchor_id=anchor.id,
            org_id="solo",
            direction="before",
            limit=2,
            max_hours=2.0,
        )
        assert anchor_out is not None
        ids = [m.id for m in adjacent]
        # Two earlier events, ASC.
        assert ids == [rows[0].id, rows[1].id]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_timeline_around_direction_after(_sqlite_url: str):
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.factory import make_store

    store = await make_store(_sqlite_url)
    try:
        await store._conn.execute(
            "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
            ("solo", "Solo"),
        )
        await store._conn.commit()
        base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
        rows = []
        for i, off in enumerate([-30, -15, 0, 15, 30]):
            rows.append(
                await _insert_with_ts(
                    store,
                    org_id="solo",
                    project="p",
                    content=f"event-{i}",
                    created_at_ts=_ts(base, minutes=off),
                    seed=i,
                )
            )
        anchor = rows[2]

        anchor_out, adjacent = await store.list_timeline_around(
            anchor_id=anchor.id,
            org_id="solo",
            direction="after",
            limit=2,
            max_hours=2.0,
        )
        assert anchor_out is not None
        ids = [m.id for m in adjacent]
        assert ids == [rows[3].id, rows[4].id]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_timeline_around_unknown_anchor(_sqlite_url: str):
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.factory import make_store

    store = await make_store(_sqlite_url)
    try:
        await store._conn.execute(
            "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
            ("solo", "Solo"),
        )
        await store._conn.commit()
        anchor_out, adjacent = await store.list_timeline_around(
            anchor_id="mem_nope",
            org_id="solo",
            direction="both",
            limit=10,
            max_hours=2.0,
        )
        assert anchor_out is None
        assert adjacent == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_timeline_around_wrong_org_returns_none(_sqlite_url: str):
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.factory import make_store

    store = await make_store(_sqlite_url)
    try:
        for org_id in ("solo", "org_a"):
            await store._conn.execute(
                "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
                (org_id, org_id),
            )
        await store._conn.commit()
        base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
        anchor = await _insert_with_ts(
            store,
            org_id="solo",
            project="p",
            content="anchor",
            created_at_ts=_ts(base),
            seed=0,
        )
        # Anchor exists but caller has a different org_id.
        anchor_out, adjacent = await store.list_timeline_around(
            anchor_id=anchor.id,
            org_id="org_a",
            direction="both",
            limit=10,
            max_hours=2.0,
        )
        assert anchor_out is None
        assert adjacent == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_timeline_around_anchor_with_null_project(_sqlite_url: str):
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.factory import make_store

    store = await make_store(_sqlite_url)
    try:
        await store._conn.execute(
            "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
            ("solo", "Solo"),
        )
        await store._conn.commit()
        base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
        # Anchor with no project.
        nm = NewMemory(
            org_id="solo",
            content="anchor-null-project",
            embedding=_vec(0),
            project=None,
            meta={"type": "note"},
        )
        anchor = await store.insert_memory(nm)
        await store._conn.execute(
            "UPDATE memories SET created_at = ? WHERE id = ?",
            (_ts(base), anchor.id),
        )
        await store._conn.commit()
        # Adjacent row in some project.
        await _insert_with_ts(
            store,
            org_id="solo",
            project="p",
            content="other",
            created_at_ts=_ts(base, minutes=10),
            seed=1,
        )

        anchor_out, adjacent = await store.list_timeline_around(
            anchor_id=anchor.id,
            org_id="solo",
            direction="both",
            limit=10,
            max_hours=2.0,
        )
        assert anchor_out is not None
        assert anchor_out.project is None
        # NULL anchor.project → no adjacent rows.
        assert adjacent == []
    finally:
        await store.close()
