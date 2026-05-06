"""Service-level tests for lore.services.workspaces using a real Postgres store."""

from __future__ import annotations

import uuid

import pytest

from lore.persistence import IntegrityError, StoreNotFoundError
from lore.services.workspaces import (
    WORKSPACE_ROLES,
    add_member,
    archive_workspace,
    create_workspace,
    get_workspace,
    has_ws_permission,
    list_members,
    list_workspaces,
    remove_member,
    replace_workspace,
    update_member_role,
    update_workspace,
)
from lore.persistence import WorkspacePatch

# ── helpers ───────────────────────────────────────────────────────────────────

_ORG = "solo"
_OTHER_ORG = "other-org"


def _slug() -> str:
    """Generate a unique slug for each test."""
    return f"ws-{uuid.uuid4().hex[:8]}"


async def _make_ws(store, *, org_id=_ORG, name="test-workspace", **kw):
    slug = kw.pop("slug", _slug())
    return await create_workspace(store, org_id=org_id, name=name, slug=slug, **kw)


# ── create / get round-trip ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_then_get(store):
    """Happy path: create a workspace then get it back."""
    ws = await _make_ws(store, name="my-workspace")
    fetched = await get_workspace(store, ws.id, _ORG)
    assert fetched.id == ws.id
    assert fetched.name == "my-workspace"
    assert fetched.org_id == _ORG
    assert fetched.archived_at is None


@pytest.mark.asyncio
async def test_create_workspace_slug_conflict_raises_integrity(store):
    """Same (org_id, slug) twice → IntegrityError."""
    slug = _slug()
    await create_workspace(store, org_id=_ORG, name="first", slug=slug)
    with pytest.raises(IntegrityError):
        await create_workspace(store, org_id=_ORG, name="second", slug=slug)


@pytest.mark.asyncio
async def test_get_workspace_not_found_raises(store):
    """Random id → StoreNotFoundError."""
    with pytest.raises(StoreNotFoundError):
        await get_workspace(store, "00000000-0000-0000-0000-000000000000", _ORG)


@pytest.mark.asyncio
async def test_get_workspace_org_mismatch_raises(store):
    """Get under wrong org → StoreNotFoundError."""
    ws = await _make_ws(store)
    with pytest.raises(StoreNotFoundError):
        await get_workspace(store, ws.id, _OTHER_ORG)


# ── update_workspace ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_workspace_changes_name(store):
    """Happy path: update workspace name."""
    ws = await _make_ws(store, name="old-name")
    updated = await update_workspace(store, ws.id, _ORG, WorkspacePatch(name="new-name"))
    assert updated.name == "new-name"
    assert updated.id == ws.id


@pytest.mark.asyncio
async def test_update_workspace_empty_patch_raises_value_error(store):
    """Empty WorkspacePatch → ValueError."""
    ws = await _make_ws(store)
    with pytest.raises(ValueError):
        await update_workspace(store, ws.id, _ORG, WorkspacePatch())


@pytest.mark.asyncio
async def test_update_workspace_not_found_raises(store):
    """Update non-existent workspace → StoreNotFoundError."""
    with pytest.raises(StoreNotFoundError):
        await update_workspace(
            store,
            "00000000-0000-0000-0000-000000000000",
            _ORG,
            WorkspacePatch(name="x"),
        )


# ── replace_workspace ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replace_workspace_sets_name_and_settings(store):
    """replace_workspace (PUT) sets name and settings atomically."""
    ws = await _make_ws(store, name="before", settings={"k": "v"})
    updated = await replace_workspace(
        store, ws.id, _ORG, name="after", settings={"x": 1}
    )
    assert updated.name == "after"
    assert updated.settings == {"x": 1}


# ── archive_workspace ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_workspace_idempotent(store):
    """Archive twice; second call is silent (no error)."""
    ws = await _make_ws(store)
    await archive_workspace(store, ws.id, _ORG)
    # second call must not raise
    await archive_workspace(store, ws.id, _ORG)


@pytest.mark.asyncio
async def test_archive_workspace_not_found_raises(store):
    """Archive non-existent workspace → StoreNotFoundError."""
    with pytest.raises(StoreNotFoundError):
        await archive_workspace(store, "00000000-0000-0000-0000-000000000000", _ORG)


# ── add_member ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_member_happy(store):
    """Create workspace, add member, verify returned StoredMember."""
    ws = await _make_ws(store)
    member = await add_member(store, ws.id, _ORG, user_id="user-abc", role="member")
    assert member.workspace_id == ws.id
    assert member.user_id == "user-abc"
    assert member.role == "member"


@pytest.mark.asyncio
async def test_add_member_invalid_role_raises(store):
    """add_member with invalid role → ValueError."""
    ws = await _make_ws(store)
    with pytest.raises(ValueError, match="Invalid role"):
        await add_member(store, ws.id, _ORG, user_id="user-abc", role="superuser")


@pytest.mark.asyncio
async def test_add_member_to_missing_workspace_raises_not_found(store):
    """add_member to non-existent workspace → StoreNotFoundError."""
    with pytest.raises(StoreNotFoundError):
        await add_member(
            store,
            "00000000-0000-0000-0000-000000000000",
            _ORG,
            user_id="user-abc",
        )


# ── list_members ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_members_returns_only_targeted_workspace(store):
    """list_members returns only members of the given workspace."""
    ws1 = await _make_ws(store)
    ws2 = await _make_ws(store)
    await add_member(store, ws1.id, _ORG, user_id="user-1")
    await add_member(store, ws1.id, _ORG, user_id="user-2")
    await add_member(store, ws2.id, _ORG, user_id="user-3")

    members = await list_members(store, ws1.id, _ORG)
    user_ids = {m.user_id for m in members}
    assert "user-1" in user_ids
    assert "user-2" in user_ids
    assert "user-3" not in user_ids


# ── update_member_role ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_member_role_changes_role(store):
    """update_member_role returns the member with the new role."""
    ws = await _make_ws(store)
    await add_member(store, ws.id, _ORG, user_id="user-x", role="viewer")
    updated = await update_member_role(store, ws.id, _ORG, user_id="user-x", role="admin")
    assert updated.role == "admin"
    assert updated.user_id == "user-x"


@pytest.mark.asyncio
async def test_update_member_role_not_found_raises(store):
    """Updating role for non-existent member → StoreNotFoundError."""
    ws = await _make_ws(store)
    with pytest.raises(StoreNotFoundError):
        await update_member_role(
            store, ws.id, _ORG, user_id="no-such-user", role="member"
        )


# ── remove_member ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_member_happy(store):
    """remove_member succeeds and the member is gone."""
    ws = await _make_ws(store)
    await add_member(store, ws.id, _ORG, user_id="user-rm")
    await remove_member(store, ws.id, _ORG, user_id="user-rm")
    members = await list_members(store, ws.id, _ORG)
    assert all(m.user_id != "user-rm" for m in members)


@pytest.mark.asyncio
async def test_remove_member_not_found_raises(store):
    """Removing non-existent member → StoreNotFoundError."""
    ws = await _make_ws(store)
    with pytest.raises(StoreNotFoundError):
        await remove_member(store, ws.id, _ORG, user_id="ghost-user")


# ── has_ws_permission helper ──────────────────────────────────────────────────


def test_has_ws_permission_helper():
    """Table-driven: viewer < member < admin < owner."""
    # Each role meets its own minimum
    for role in WORKSPACE_ROLES:
        assert has_ws_permission(role, role), f"{role} should meet {role}"

    # viewer is below all others
    assert not has_ws_permission("viewer", "member")
    assert not has_ws_permission("viewer", "admin")
    assert not has_ws_permission("viewer", "owner")

    # member is above viewer but below admin/owner
    assert has_ws_permission("member", "viewer")
    assert not has_ws_permission("member", "admin")
    assert not has_ws_permission("member", "owner")

    # admin is above viewer and member, below owner
    assert has_ws_permission("admin", "viewer")
    assert has_ws_permission("admin", "member")
    assert not has_ws_permission("admin", "owner")

    # owner is above all
    assert has_ws_permission("owner", "viewer")
    assert has_ws_permission("owner", "member")
    assert has_ws_permission("owner", "admin")

    # Unknown roles
    assert not has_ws_permission("unknown", "viewer")
    assert not has_ws_permission("viewer", "unknown")
