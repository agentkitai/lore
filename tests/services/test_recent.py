"""Service tests for lore.services.recent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lore.persistence import NewMemory
from lore.services import recent  # noqa: E402

# ── helpers ───────────────────────────────────────────────────────────────────


def _vec(seed: int = 0) -> list[float]:
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


async def _ensure_org(store, org_id: str) -> None:
    await store._conn.execute(
        "INSERT INTO orgs (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        org_id,
        org_id,
    )


async def _insert_memory(
    store,
    *,
    org_id: str = "solo",
    content: str = "test memory",
    project: str | None = None,
    created_at: datetime | None = None,
) -> str:
    """Insert a memory and return its id. Optionally backdates created_at via raw SQL."""
    await _ensure_org(store, org_id)
    mem = await store.insert_memory(
        NewMemory(
            org_id=org_id,
            content=content,
            embedding=_vec(1),
            project=project,
        )
    )
    if created_at is not None:
        await store._conn.execute(
            "UPDATE memories SET created_at = $1 WHERE id = $2",
            created_at,
            mem.id,
        )
    return mem.id


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_returns_recent_memories(store):
    """Memories within the window are returned; older ones are excluded."""
    org = "recent-t1"
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)

    await _insert_memory(store, org_id=org, content="now-1", created_at=None)
    await _insert_memory(store, org_id=org, content="now-2", created_at=None)
    await _insert_memory(store, org_id=org, content="old-1", created_at=old)

    results = await recent.get_recent_activity(
        store, org_id=org, project=None, hours=24
    )
    contents = {m.content for m in results}
    assert "now-1" in contents
    assert "now-2" in contents
    assert "old-1" not in contents


@pytest.mark.asyncio
async def test_filters_by_project(store):
    """Project filter constrains results to the given project."""
    org = "recent-t2"
    await _insert_memory(store, org_id=org, content="proj-a-mem", project="proj-a")
    await _insert_memory(store, org_id=org, content="proj-b-mem", project="proj-b")

    results = await recent.get_recent_activity(
        store, org_id=org, project="proj-a", hours=24
    )
    contents = {m.content for m in results}
    assert "proj-a-mem" in contents
    assert "proj-b-mem" not in contents


@pytest.mark.asyncio
async def test_respects_max_memories_cap(store):
    """max_memories caps the number of returned memories."""
    org = "recent-t3"
    for i in range(5):
        await _insert_memory(store, org_id=org, content=f"mem-{i}")

    results = await recent.get_recent_activity(
        store, org_id=org, project=None, hours=24, max_memories=3
    )
    assert len(results) == 3
