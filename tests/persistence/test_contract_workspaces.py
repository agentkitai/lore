"""Contract tests for the WorkspaceOps slice of Store — get_workspace / list_workspaces.

These tests run against every Store implementation (Phase 1D: Postgres only).
"""

from __future__ import annotations

import pytest

from lore.persistence import Store
from lore.persistence.types import StoredWorkspace

# ── helpers ────────────────────────────────────────────────────────────────────


async def _insert_workspace(
    store,
    *,
    org_id: str = "solo",
    workspace_id: str | None = None,
    name: str = "ws",
    slug: str = "ws",
    archived: bool = False,
) -> str:
    """Insert a workspace row via raw SQL and return its id."""
    from ulid import ULID

    ws_id = workspace_id or f"ws_{ULID()}"
    conn = store._conn
    await conn.execute(
        """
        INSERT INTO workspaces (id, org_id, name, slug, settings)
        VALUES ($1, $2, $3, $4, '{}'::jsonb)
        """,
        ws_id,
        org_id,
        name,
        slug,
    )
    if archived:
        await conn.execute(
            "UPDATE workspaces SET archived_at = now() WHERE id = $1",
            ws_id,
        )
    return ws_id


# ── get_workspace tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_workspace_round_trip(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-rt", name="Round Trip WS", slug="round-trip-ws")
    result = await store.get_workspace(ws_id, "org-rt")
    assert result is not None
    assert isinstance(result, StoredWorkspace)
    assert result.id == ws_id
    assert result.org_id == "org-rt"
    assert result.name == "Round Trip WS"
    assert result.slug == "round-trip-ws"
    assert result.settings == {}
    assert result.created_at is not None
    assert result.archived_at is None


@pytest.mark.asyncio
async def test_get_workspace_returns_none_when_missing(store: Store):
    result = await store.get_workspace("ws_nonexistent_000", "org-x")
    assert result is None


@pytest.mark.asyncio
async def test_get_workspace_org_isolation(store: Store):
    ws_id = await _insert_workspace(store, org_id="org_a", name="Org A WS", slug="org-a-ws")
    result = await store.get_workspace(ws_id, "org_b")
    assert result is None


# ── list_workspaces tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_workspaces_returns_active_only_by_default(store: Store):
    await _insert_workspace(store, org_id="org-list-1", name="Active WS", slug="active-ws")
    await _insert_workspace(store, org_id="org-list-1", name="Archived WS", slug="archived-ws", archived=True)

    result = await store.list_workspaces("org-list-1")
    names = [ws.name for ws in result]
    assert "Active WS" in names
    assert "Archived WS" not in names


@pytest.mark.asyncio
async def test_list_workspaces_with_include_archived(store: Store):
    await _insert_workspace(store, org_id="org-list-2", name="Active WS", slug="active-ws-2")
    await _insert_workspace(store, org_id="org-list-2", name="Archived WS", slug="archived-ws-2", archived=True)

    result = await store.list_workspaces("org-list-2", include_archived=True)
    names = [ws.name for ws in result]
    assert "Active WS" in names
    assert "Archived WS" in names
    assert len(names) == 2


@pytest.mark.asyncio
async def test_list_workspaces_org_isolation(store: Store):
    await _insert_workspace(store, org_id="org-iso-a", name="Org A WS", slug="org-iso-a-ws")
    await _insert_workspace(store, org_id="org-iso-b", name="Org B WS", slug="org-iso-b-ws")

    result_a = await store.list_workspaces("org-iso-a")
    result_b = await store.list_workspaces("org-iso-b")

    assert all(ws.org_id == "org-iso-a" for ws in result_a)
    assert all(ws.org_id == "org-iso-b" for ws in result_b)
    assert len(result_a) == 1
    assert len(result_b) == 1


@pytest.mark.asyncio
async def test_list_workspaces_ordered_by_name(store: Store):
    await _insert_workspace(store, org_id="org-order", name="Zeta WS", slug="zeta-ws")
    await _insert_workspace(store, org_id="org-order", name="Alpha WS", slug="alpha-ws")
    await _insert_workspace(store, org_id="org-order", name="Mu WS", slug="mu-ws")

    result = await store.list_workspaces("org-order")
    names = [ws.name for ws in result]
    assert names == sorted(names)
