"""Service-level tests for lore.services.lessons.

Uses a real Postgres store (via conftest fixture) for integration tests,
and direct monkeypatching for unit-style tests that need controlled recall results.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lore.persistence import ScoredMemory
from lore.persistence.exceptions import StoreNotFoundError
from lore.services import lessons

# ── Helpers ──────────────────────────────────────────────────────────


def _make_scored_memory(
    *,
    memory_id: str = "abc",
    org_id: str = "solo",
    content: str = "problem text",
    context: str = "resolution text",
    score: float = 0.9,
    importance_score: float = 1.0,
    created_at: datetime | None = None,
    last_accessed_at: datetime | None = None,
    tags: tuple = (),
    confidence: float = 0.8,
    source: str | None = None,
    project: str | None = None,
    upvotes: int = 0,
    downvotes: int = 0,
    meta: dict | None = None,
) -> ScoredMemory:
    now = datetime.now(timezone.utc)
    return ScoredMemory(
        id=memory_id,
        org_id=org_id,
        content=content,
        context=context,
        tags=tags,
        confidence=confidence,
        source=source,
        project=project,
        created_at=created_at or now,
        updated_at=now,
        expires_at=None,
        upvotes=upvotes,
        downvotes=downvotes,
        meta=meta or {},
        importance_score=importance_score,
        access_count=0,
        last_accessed_at=last_accessed_at,
        score=score,
    )


# ── create ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_inserts_with_field_translation(store):
    """problem→content, resolution→context at the persistence boundary."""
    lesson_id = await lessons.create(
        store,
        org_id="solo",
        problem="The problem statement",
        resolution="The resolution",
        context="legacy context field",
        tags=["a", "b"],
        confidence=0.9,
        source="manual",
        project=None,
        embedding=None,
        expires_at=None,
        meta={"type": "lesson"},
    )
    fetched = await store.get_memory("solo", lesson_id)
    assert fetched is not None
    assert fetched.content == "The problem statement"
    assert fetched.context == "The resolution"


@pytest.mark.asyncio
async def test_create_drops_context_field_silently(store):
    """The wire-level `context` field is never stored; resolution wins."""
    lesson_id = await lessons.create(
        store,
        org_id="solo",
        problem="prob",
        resolution="res",
        context="this should be ignored",
        tags=[],
        confidence=0.5,
        source=None,
        project=None,
        embedding=None,
        expires_at=None,
        meta={},
    )
    fetched = await store.get_memory("solo", lesson_id)
    assert fetched is not None
    # context field stores resolution, not the `context` arg
    assert fetched.context == "res"
    assert fetched.content == "prob"


# ── search ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_applies_time_decay(store, monkeypatch):
    """Older memories with same base score should rank lower after time-decay."""
    now = datetime.now(timezone.utc)
    # Recent memory — created 1 day ago
    recent = _make_scored_memory(
        memory_id="recent",
        score=0.8,
        importance_score=1.0,
        created_at=now - timedelta(days=1),
        meta={"type": "lesson"},  # half_life=30
    )
    # Old memory — created 60 days ago (same base score)
    old = _make_scored_memory(
        memory_id="old",
        score=0.8,
        importance_score=1.0,
        created_at=now - timedelta(days=60),
        meta={"type": "lesson"},  # half_life=30
    )

    async def fake_recall(params):
        return [recent, old]

    monkeypatch.setattr(store, "recall_by_embedding", fake_recall)

    results = await lessons.search(
        store,
        org_id="solo",
        embedding=[0.0] * 384,
        project=None,
        tags=[],
        limit=5,
        min_confidence=0.0,
    )

    assert len(results) == 2
    # Recent should rank first (higher decay-adjusted score)
    assert results[0]["id"] == "recent"
    assert results[1]["id"] == "old"
    # old memory's score should be significantly lower
    assert results[0]["score"] > results[1]["score"]


@pytest.mark.asyncio
async def test_search_filters_below_min_confidence(store, monkeypatch):
    """Memories whose final score < min_confidence are excluded."""
    now = datetime.now(timezone.utc)
    high = _make_scored_memory(memory_id="high", score=0.9, created_at=now)
    low = _make_scored_memory(memory_id="low", score=0.1, created_at=now)

    async def fake_recall(params):
        return [high, low]

    monkeypatch.setattr(store, "recall_by_embedding", fake_recall)

    results = await lessons.search(
        store,
        org_id="solo",
        embedding=[0.0] * 384,
        project=None,
        tags=[],
        limit=5,
        min_confidence=0.5,
    )

    ids = [r["id"] for r in results]
    assert "high" in ids
    assert "low" not in ids


# ── record_access ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_access_returns_dict(store):
    """Happy-path: returns dict with access_count and last_accessed_at."""
    lesson_id = await lessons.create(
        store,
        org_id="solo",
        problem="access test",
        resolution="",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project=None,
        embedding=None,
        expires_at=None,
        meta={},
    )
    result = await lessons.record_access(
        store, org_id="solo", lesson_id=lesson_id, project=None
    )
    assert result["id"] == lesson_id
    assert result["access_count"] == 1
    assert result["last_accessed_at"] is not None
    assert "importance_score" in result


@pytest.mark.asyncio
async def test_record_access_404_on_missing(store):
    """Missing lesson raises StoreNotFoundError."""
    with pytest.raises(StoreNotFoundError):
        await lessons.record_access(
            store,
            org_id="solo",
            lesson_id="00000000-0000-0000-0000-000000000000",
            project=None,
        )


@pytest.mark.asyncio
async def test_record_access_404_on_project_mismatch(store):
    """Lesson in project A is not accessible when requesting project B."""
    lesson_id = await lessons.create(
        store,
        org_id="solo",
        problem="proj test",
        resolution="",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project="project-a",
        embedding=None,
        expires_at=None,
        meta={},
    )
    with pytest.raises(StoreNotFoundError):
        await lessons.record_access(
            store, org_id="solo", lesson_id=lesson_id, project="project-b"
        )


# ── get ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_stored_memory(store):
    """get() returns StoredMemory with correct field translation."""
    lesson_id = await lessons.create(
        store,
        org_id="solo",
        problem="get me",
        resolution="got it",
        context=None,
        tags=["x"],
        confidence=0.7,
        source="test",
        project=None,
        embedding=None,
        expires_at=None,
        meta={},
    )
    mem = await lessons.get(
        store, org_id="solo", lesson_id=lesson_id, project=None
    )
    assert mem.id == lesson_id
    assert mem.content == "get me"
    assert mem.context == "got it"


@pytest.mark.asyncio
async def test_get_404_on_project_mismatch(store):
    """get() with wrong project raises StoreNotFoundError."""
    lesson_id = await lessons.create(
        store,
        org_id="solo",
        problem="proj get",
        resolution="",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project="project-a",
        embedding=None,
        expires_at=None,
        meta={},
    )
    with pytest.raises(StoreNotFoundError):
        await lessons.get(
            store, org_id="solo", lesson_id=lesson_id, project="wrong-project"
        )


# ── update ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_changes_confidence(store):
    """update() patches confidence and returns the updated StoredMemory."""
    lesson_id = await lessons.create(
        store,
        org_id="solo",
        problem="update me",
        resolution="",
        context=None,
        tags=[],
        confidence=0.3,
        source=None,
        project=None,
        embedding=None,
        expires_at=None,
        meta={},
    )
    updated = await lessons.update(
        store,
        org_id="solo",
        lesson_id=lesson_id,
        project=None,
        confidence=0.9,
        tags=None,
        meta=None,
        upvotes=None,
        downvotes=None,
    )
    assert updated.confidence == pytest.approx(0.9, abs=1e-4)


@pytest.mark.asyncio
async def test_update_with_plus_one_upvote(store):
    """upvotes='+1' increments upvotes by 1."""
    lesson_id = await lessons.create(
        store,
        org_id="solo",
        problem="vote me",
        resolution="",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project=None,
        embedding=None,
        expires_at=None,
        meta={},
    )
    updated = await lessons.update(
        store,
        org_id="solo",
        lesson_id=lesson_id,
        project=None,
        confidence=None,
        tags=None,
        meta=None,
        upvotes="+1",
        downvotes=None,
    )
    assert updated.upvotes == 1


@pytest.mark.asyncio
async def test_update_with_minus_one_vote_raises(store):
    """upvotes='-1' raises ValueError (not supported in Phase 1H)."""
    lesson_id = await lessons.create(
        store,
        org_id="solo",
        problem="decrement",
        resolution="",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project=None,
        embedding=None,
        expires_at=None,
        meta={},
    )
    with pytest.raises(ValueError, match="not supported"):
        await lessons.update(
            store,
            org_id="solo",
            lesson_id=lesson_id,
            project=None,
            confidence=None,
            tags=None,
            meta=None,
            upvotes="-1",
            downvotes=None,
        )


@pytest.mark.asyncio
async def test_update_with_absolute_vote_raises(store):
    """upvotes=5 (absolute int) raises ValueError (not supported in Phase 1H)."""
    lesson_id = await lessons.create(
        store,
        org_id="solo",
        problem="absolute vote",
        resolution="",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project=None,
        embedding=None,
        expires_at=None,
        meta={},
    )
    with pytest.raises(ValueError, match="not supported"):
        await lessons.update(
            store,
            org_id="solo",
            lesson_id=lesson_id,
            project=None,
            confidence=None,
            tags=None,
            meta=None,
            upvotes=5,
            downvotes=None,
        )


@pytest.mark.asyncio
async def test_update_no_fields_raises(store):
    """Calling update() with all None fields raises ValueError."""
    lesson_id = await lessons.create(
        store,
        org_id="solo",
        problem="no fields",
        resolution="",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project=None,
        embedding=None,
        expires_at=None,
        meta={},
    )
    with pytest.raises(ValueError, match="No fields to update"):
        await lessons.update(
            store,
            org_id="solo",
            lesson_id=lesson_id,
            project=None,
            confidence=None,
            tags=None,
            meta=None,
            upvotes=None,
            downvotes=None,
        )


# ── delete ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_404_on_missing(store):
    """delete() with unknown id raises StoreNotFoundError."""
    with pytest.raises(StoreNotFoundError):
        await lessons.delete(
            store,
            org_id="solo",
            lesson_id="00000000-0000-0000-0000-000000000001",
            project=None,
        )


# ── list_lessons ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_total_and_lessons(store):
    """list_lessons() returns (total, rows) tuple."""
    await lessons.create(
        store,
        org_id="solo",
        problem="list me",
        resolution="",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project="list-proj",
        embedding=None,
        expires_at=None,
        meta={},
    )
    total, rows = await lessons.list_lessons(
        store,
        org_id="solo",
        project="list-proj",
        query=None,
        category=None,
        since=None,
        min_reputation=None,
        limit=50,
        offset=0,
    )
    assert total >= 1
    assert any(m.content == "list me" for m in rows)


# ── export ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_includes_embeddings(store):
    """export() returns ExportedMemory objects with embedding field."""
    await lessons.create(
        store,
        org_id="solo",
        problem="export me",
        resolution="exported",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project="export-proj",
        embedding=[0.1] * 384,
        expires_at=None,
        meta={},
    )
    exported = await lessons.export(
        store, org_id="solo", project="export-proj"
    )
    assert len(exported) >= 1
    mem = next(m for m in exported if m.content == "export me")
    # ExportedMemory has an embedding field
    assert hasattr(mem, "embedding")
    assert mem.context == "exported"


# ── import_lessons ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_import_upserts(store):
    """import_lessons() inserts records and returns count processed."""
    from types import SimpleNamespace

    lesson = SimpleNamespace(
        id=None,
        problem="imported problem",
        resolution="imported resolution",
        tags=["imported"],
        confidence=0.8,
        source="import",
        project="import-proj",
        embedding=[0.0] * 384,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )
    count = await lessons.import_lessons(
        store,
        org_id="solo",
        lessons=[lesson],
        project_override=None,
    )
    assert count == 1

    # Verify it was stored
    total, rows = await lessons.list_lessons(
        store,
        org_id="solo",
        project="import-proj",
        query=None,
        category=None,
        since=None,
        min_reputation=None,
        limit=50,
        offset=0,
    )
    assert any(m.content == "imported problem" for m in rows)


@pytest.mark.asyncio
async def test_import_uses_project_override(store):
    """project_override takes precedence over lesson.project."""
    from types import SimpleNamespace

    lesson = SimpleNamespace(
        id=None,
        problem="override test",
        resolution="",
        tags=[],
        confidence=0.5,
        source=None,
        project="original-project",
        embedding=None,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )
    await lessons.import_lessons(
        store,
        org_id="solo",
        lessons=[lesson],
        project_override="override-project",
    )

    # Should appear under override-project, not original-project
    total_override, rows_override = await lessons.list_lessons(
        store,
        org_id="solo",
        project="override-project",
        query=None,
        category=None,
        since=None,
        min_reputation=None,
        limit=50,
        offset=0,
    )
    total_original, rows_original = await lessons.list_lessons(
        store,
        org_id="solo",
        project="original-project",
        query=None,
        category=None,
        since=None,
        min_reputation=None,
        limit=50,
        offset=0,
    )
    assert any(m.content == "override test" for m in rows_override)
    assert not any(m.content == "override test" for m in rows_original)
