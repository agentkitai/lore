"""Service-level tests for lore.services.snapshots using a real Postgres store."""

from __future__ import annotations

import pytest

from lore.services.snapshots import create_snapshot

# ── helpers ───────────────────────────────────────────────────────────────────

_ORG = "solo"

_LONG_CONTENT = "A" * 100  # 100 chars — deliberately over the 80-char truncation threshold


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_snapshot_inserts_with_session_snapshot_tags(store):
    """create_snapshot with explicit session_id → tags include 'session_snapshot' and the id."""
    snap = await create_snapshot(
        store,
        org_id=_ORG,
        content="session content here",
        session_id="abc123",
    )
    fetched = await store.get_memory(_ORG, snap.id)
    assert fetched is not None
    assert "session_snapshot" in fetched.tags
    assert "abc123" in fetched.tags


@pytest.mark.asyncio
async def test_create_snapshot_generates_session_id_when_missing(store):
    """create_snapshot without session_id → tags has 2+ entries, meta['session_id'] is non-empty."""
    snap = await create_snapshot(
        store,
        org_id=_ORG,
        content="auto session content",
    )
    assert len(snap.tags) >= 2
    assert snap.meta["session_id"] != ""
    assert snap.meta["session_id"] is not None


@pytest.mark.asyncio
async def test_create_snapshot_uses_provided_session_id(store):
    """create_snapshot with explicit session_id → meta['session_id'] matches what was provided."""
    snap = await create_snapshot(
        store,
        org_id=_ORG,
        content="explicit session content",
        session_id="mysession42",
    )
    assert snap.meta["session_id"] == "mysession42"


@pytest.mark.asyncio
async def test_create_snapshot_meta_contains_type_and_tier(store):
    """meta must contain type='session_snapshot' and tier='long' (bug-fixed location)."""
    snap = await create_snapshot(
        store,
        org_id=_ORG,
        content="type and tier check",
        session_id="tiersess",
    )
    assert snap.meta["type"] == "session_snapshot"
    assert snap.meta["tier"] == "long"


@pytest.mark.asyncio
async def test_create_snapshot_default_title_is_truncated_content(store):
    """No title provided, content > 80 chars → meta['title'] is at most 80 chars."""
    snap = await create_snapshot(
        store,
        org_id=_ORG,
        content=_LONG_CONTENT,
        session_id="truncsess",
    )
    assert snap.meta["title"] == _LONG_CONTENT[:80].strip()
    assert len(snap.meta["title"]) <= 80


@pytest.mark.asyncio
async def test_create_snapshot_explicit_title_preserved(store):
    """Explicit title → meta['title'] matches exactly what was passed."""
    snap = await create_snapshot(
        store,
        org_id=_ORG,
        content="some content",
        title="My Custom Title",
        session_id="titlesess",
    )
    assert snap.meta["title"] == "My Custom Title"


@pytest.mark.asyncio
async def test_create_snapshot_passes_through_project(store):
    """project parameter → stored memory has the correct project."""
    snap = await create_snapshot(
        store,
        org_id=_ORG,
        content="project content",
        session_id="projsess",
        project="my-project",
    )
    assert snap.project == "my-project"


@pytest.mark.asyncio
async def test_create_snapshot_appends_user_tags(store):
    """User-supplied tags are appended after 'session_snapshot' and the session_id."""
    snap = await create_snapshot(
        store,
        org_id=_ORG,
        content="tagged content",
        session_id="tagsess",
        tags=["custom1", "custom2"],
    )
    assert "session_snapshot" in snap.tags
    assert "tagsess" in snap.tags
    assert "custom1" in snap.tags
    assert "custom2" in snap.tags
