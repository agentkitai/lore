"""Contract tests for the WorkspaceOps slice of Store — get_workspace / list_workspaces.

These tests run against every Store implementation (Phase 1D: Postgres only).
"""

from __future__ import annotations

import asyncio

import pytest

from lore.persistence import Store
from lore.persistence.exceptions import IntegrityError
from lore.persistence.types import NewMember, NewWorkspace, StoredMember, StoredWorkspace, WorkspacePatch

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


# ── create_workspace tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_workspace_round_trip(store: Store):
    ws = NewWorkspace(org_id="org-crt", name="Created WS", slug="created-ws", settings={"key": "val"})
    result = await store.create_workspace(ws)
    assert isinstance(result, StoredWorkspace)
    assert result.id.startswith("ws_")
    assert result.org_id == "org-crt"
    assert result.name == "Created WS"
    assert result.slug == "created-ws"
    assert result.settings == {"key": "val"}
    assert result.created_at is not None
    assert result.archived_at is None

    fetched = await store.get_workspace(result.id, "org-crt")
    assert fetched is not None
    assert fetched.id == result.id
    assert fetched.settings == {"key": "val"}


@pytest.mark.asyncio
async def test_create_workspace_slug_conflict_raises_integrity(store: Store):
    ws1 = NewWorkspace(org_id="org-slug-clash", name="WS One", slug="clash-slug")
    await store.create_workspace(ws1)

    ws2 = NewWorkspace(org_id="org-slug-clash", name="WS Two", slug="clash-slug")
    with pytest.raises(IntegrityError, match="clash-slug"):
        await store.create_workspace(ws2)


@pytest.mark.asyncio
async def test_create_workspace_same_slug_different_orgs_allowed(store: Store):
    ws_a = NewWorkspace(org_id="org_a_slug", name="WS A", slug="shared-slug")
    ws_b = NewWorkspace(org_id="org_b_slug", name="WS B", slug="shared-slug")
    result_a = await store.create_workspace(ws_a)
    result_b = await store.create_workspace(ws_b)
    assert result_a.id != result_b.id
    assert result_a.slug == result_b.slug


# ── update_workspace tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_workspace_single_field(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-upd", name="Original Name", slug="upd-ws")
    patch = WorkspacePatch(name="Updated Name")
    result = await store.update_workspace(ws_id, "org-upd", patch)
    assert result is not None
    assert result.name == "Updated Name"
    assert result.slug == "upd-ws"


@pytest.mark.asyncio
async def test_update_workspace_settings(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-upd-s", name="Settings WS", slug="settings-ws")
    patch = WorkspacePatch(settings={"theme": "dark", "lang": "en"})
    result = await store.update_workspace(ws_id, "org-upd-s", patch)
    assert result is not None
    assert result.settings == {"theme": "dark", "lang": "en"}


@pytest.mark.asyncio
async def test_update_workspace_returns_none_when_missing(store: Store):
    patch = WorkspacePatch(name="Ghost")
    result = await store.update_workspace("ws_nonexistent_999", "org-x", patch)
    assert result is None


@pytest.mark.asyncio
async def test_update_workspace_empty_patch_raises_value_error(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-empty", name="Empty Patch WS", slug="empty-patch-ws")
    patch = WorkspacePatch()
    with pytest.raises(ValueError, match="empty patch"):
        await store.update_workspace(ws_id, "org-empty", patch)


@pytest.mark.asyncio
async def test_update_workspace_org_isolation(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-real", name="Real Org WS", slug="real-org-ws")
    patch = WorkspacePatch(name="Hijacked")
    result = await store.update_workspace(ws_id, "org-wrong", patch)
    assert result is None

    # original unchanged
    original = await store.get_workspace(ws_id, "org-real")
    assert original is not None
    assert original.name == "Real Org WS"


# ── archive_workspace tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_workspace_marks_archived_at(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-arch", name="To Archive", slug="to-archive")
    result = await store.archive_workspace(ws_id, "org-arch")
    assert result is True

    fetched = await store.get_workspace(ws_id, "org-arch")
    assert fetched is not None
    assert fetched.archived_at is not None

    listed = await store.list_workspaces("org-arch", include_archived=False)
    ids = [ws.id for ws in listed]
    assert ws_id not in ids


@pytest.mark.asyncio
async def test_archive_workspace_returns_true_when_active(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-arch-t", name="Active", slug="active-arch")
    result = await store.archive_workspace(ws_id, "org-arch-t")
    assert result is True


@pytest.mark.asyncio
async def test_archive_workspace_returns_false_when_already_archived(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-arch-f", name="Already Archived", slug="already-arch", archived=True)
    result = await store.archive_workspace(ws_id, "org-arch-f")
    assert result is False


@pytest.mark.asyncio
async def test_archive_workspace_org_isolation(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-arch-iso", name="Isolated", slug="isolated-arch")
    result = await store.archive_workspace(ws_id, "org-wrong-arch")
    assert result is False

    # still active under correct org
    fetched = await store.get_workspace(ws_id, "org-arch-iso")
    assert fetched is not None
    assert fetched.archived_at is None


# ── workspace member tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_member_round_trip(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-mem-rt", name="Member RT WS", slug="mem-rt-ws")
    member = NewMember(workspace_id=ws_id, user_id="user_001", role="writer")
    result = await store.add_workspace_member(member)
    assert isinstance(result, StoredMember)
    assert result.id.startswith("wsm_")
    assert result.workspace_id == ws_id
    assert result.user_id == "user_001"
    assert result.role == "writer"
    assert result.invited_at is not None
    assert result.accepted_at is None


@pytest.mark.asyncio
async def test_add_member_with_invalid_workspace_id_raises_integrity(store: Store):
    member = NewMember(workspace_id="ws_nonexistent_999", user_id="user_x", role="writer")
    with pytest.raises(IntegrityError, match="ws_nonexistent_999"):
        await store.add_workspace_member(member)


@pytest.mark.asyncio
async def test_list_members_returns_only_targeted_workspace(store: Store):
    ws_a = await _insert_workspace(store, org_id="org-mem-list", name="WS A", slug="mem-list-a")
    ws_b = await _insert_workspace(store, org_id="org-mem-list", name="WS B", slug="mem-list-b")
    await store.add_workspace_member(NewMember(workspace_id=ws_a, user_id="user_a1", role="writer"))
    await store.add_workspace_member(NewMember(workspace_id=ws_a, user_id="user_a2", role="reader"))
    await store.add_workspace_member(NewMember(workspace_id=ws_b, user_id="user_b1", role="admin"))

    result = await store.list_workspace_members(ws_a)
    user_ids = [m.user_id for m in result]
    assert "user_a1" in user_ids
    assert "user_a2" in user_ids
    assert "user_b1" not in user_ids
    assert all(m.workspace_id == ws_a for m in result)


@pytest.mark.asyncio
async def test_list_members_ordered_by_invited_at(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-mem-order", name="Order WS", slug="mem-order-ws")
    # Insert members with slight delay to ensure ordering
    m1 = await store.add_workspace_member(NewMember(workspace_id=ws_id, user_id="user_first", role="writer"))
    await asyncio.sleep(0.01)
    m2 = await store.add_workspace_member(NewMember(workspace_id=ws_id, user_id="user_second", role="reader"))
    await asyncio.sleep(0.01)
    m3 = await store.add_workspace_member(NewMember(workspace_id=ws_id, user_id="user_third", role="admin"))

    result = await store.list_workspace_members(ws_id)
    assert len(result) == 3
    # Should be ordered by invited_at ascending
    assert result[0].user_id == "user_first"
    assert result[1].user_id == "user_second"
    assert result[2].user_id == "user_third"


@pytest.mark.asyncio
async def test_update_member_role_changes_role(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-mem-upd", name="Update Role WS", slug="mem-upd-ws")
    await store.add_workspace_member(NewMember(workspace_id=ws_id, user_id="user_upd", role="writer"))

    result = await store.update_workspace_member_role(ws_id, "user_upd", "admin")
    assert result is not None
    assert isinstance(result, StoredMember)
    assert result.workspace_id == ws_id
    assert result.user_id == "user_upd"
    assert result.role == "admin"


@pytest.mark.asyncio
async def test_update_member_role_returns_none_when_missing(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-mem-upd-m", name="Missing Role WS", slug="mem-upd-missing-ws")
    result = await store.update_workspace_member_role(ws_id, "nonexistent_user", "admin")
    assert result is None


@pytest.mark.asyncio
async def test_remove_member_returns_true_when_existed(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-mem-del", name="Delete Member WS", slug="mem-del-ws")
    await store.add_workspace_member(NewMember(workspace_id=ws_id, user_id="user_to_del", role="writer"))

    result = await store.remove_workspace_member(ws_id, "user_to_del")
    assert result is True

    # Verify gone
    remaining = await store.list_workspace_members(ws_id)
    assert all(m.user_id != "user_to_del" for m in remaining)


@pytest.mark.asyncio
async def test_remove_member_returns_false_when_missing(store: Store):
    ws_id = await _insert_workspace(store, org_id="org-mem-del-m", name="Delete Missing WS", slug="mem-del-missing-ws")
    result = await store.remove_workspace_member(ws_id, "nonexistent_user")
    assert result is False
