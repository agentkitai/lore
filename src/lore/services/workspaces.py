"""Workspaces service — workspace + member CRUD with org-scoping and role-rank checks."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from lore.persistence import (
    NewMember,
    NewWorkspace,
    Store,
    StoredMember,
    StoredWorkspace,
    WorkspacePatch,
)
from lore.persistence.exceptions import IntegrityError, StoreNotFoundError


# ── Role hierarchy ────────────────────────────────────────────────
WORKSPACE_ROLES: tuple[str, ...] = ("viewer", "member", "admin", "owner")
_ROLE_RANK = {r: i for i, r in enumerate(WORKSPACE_ROLES)}


def has_ws_permission(role: str, minimum: str) -> bool:
    """Return True iff `role` ranks at or above `minimum` in WORKSPACE_ROLES."""
    return _ROLE_RANK.get(role, -1) >= _ROLE_RANK.get(minimum, 999)


# ── Public service functions ──────────────────────────────────────


async def list_workspaces(
    store: Store,
    org_id: str,
    *,
    include_archived: bool = False,
) -> Sequence[StoredWorkspace]:
    """Return all workspaces for an org; archived excluded by default."""
    return await store.list_workspaces(org_id, include_archived=include_archived)


async def get_workspace(
    store: Store,
    workspace_id: str,
    org_id: str,
) -> StoredWorkspace:
    """Return the workspace row or raise StoreNotFoundError."""
    row = await store.get_workspace(workspace_id, org_id)
    if row is None:
        raise StoreNotFoundError("workspaces", workspace_id)
    return row


async def create_workspace(
    store: Store,
    *,
    org_id: str,
    name: str,
    slug: str,
    settings: Optional[Mapping[str, Any]] = None,
) -> StoredWorkspace:
    """Insert a new workspace. IntegrityError propagates on slug conflict."""
    return await store.create_workspace(
        NewWorkspace(
            org_id=org_id,
            name=name,
            slug=slug,
            settings=settings or {},
        )
    )


async def update_workspace(
    store: Store,
    workspace_id: str,
    org_id: str,
    patch: WorkspacePatch,
) -> StoredWorkspace:
    """Apply a partial update to a workspace; raises StoreNotFoundError if absent.

    ValueError on empty patch propagates from store.
    """
    row = await store.update_workspace(workspace_id, org_id, patch)
    if row is None:
        raise StoreNotFoundError("workspaces", workspace_id)
    return row


async def replace_workspace(
    store: Store,
    workspace_id: str,
    org_id: str,
    *,
    name: Optional[str],
    settings: Optional[Mapping[str, Any]],
) -> StoredWorkspace:
    """Full-update (PUT) semantics: both name and settings are required.

    Raises ValueError if both are None.
    """
    if name is None and settings is None:
        raise ValueError("PUT requires name and settings")
    return await update_workspace(
        store,
        workspace_id,
        org_id,
        WorkspacePatch(name=name, settings=settings),
    )


async def archive_workspace(
    store: Store,
    workspace_id: str,
    org_id: str,
) -> None:
    """Archive a workspace. Idempotent: archiving an already-archived workspace is silent.

    Raises StoreNotFoundError if the workspace does not exist.
    """
    archived = await store.archive_workspace(workspace_id, org_id)
    if not archived:
        # Disambiguate: missing vs already archived
        row = await store.get_workspace(workspace_id, org_id)
        if row is None:
            raise StoreNotFoundError("workspaces", workspace_id)
        # Row exists but already archived — idempotent, return silently


async def add_member(
    store: Store,
    workspace_id: str,
    org_id: str,
    *,
    user_id: str,
    role: str = "member",
) -> StoredMember:
    """Add a user to a workspace with the given role.

    Verifies workspace existence first for a clean 404 path.
    Raises StoreNotFoundError if workspace is missing/wrong-org.
    Raises ValueError if role is not valid.
    IntegrityError from FK violation propagates (race condition safety).
    """
    # Verify workspace exists in-org
    ws = await store.get_workspace(workspace_id, org_id)
    if ws is None:
        raise StoreNotFoundError("workspaces", workspace_id)

    # Validate role
    if role not in WORKSPACE_ROLES:
        raise ValueError(f"Invalid role: {role!r}")

    return await store.add_workspace_member(
        NewMember(workspace_id=workspace_id, user_id=user_id, role=role)
    )


async def list_members(
    store: Store,
    workspace_id: str,
    org_id: str,
) -> Sequence[StoredMember]:
    """Return all members of a workspace.

    Verifies workspace existence first.
    """
    ws = await store.get_workspace(workspace_id, org_id)
    if ws is None:
        raise StoreNotFoundError("workspaces", workspace_id)
    return await store.list_workspace_members(workspace_id)


async def update_member_role(
    store: Store,
    workspace_id: str,
    org_id: str,
    *,
    user_id: str,
    role: str,
) -> StoredMember:
    """Update a member's role. Raises StoreNotFoundError if workspace or member absent."""
    ws = await store.get_workspace(workspace_id, org_id)
    if ws is None:
        raise StoreNotFoundError("workspaces", workspace_id)

    if role not in WORKSPACE_ROLES:
        raise ValueError(f"Invalid role: {role!r}")

    row = await store.update_workspace_member_role(workspace_id, user_id, role)
    if row is None:
        raise StoreNotFoundError("workspace_members", f"{workspace_id}/{user_id}")
    return row


async def remove_member(
    store: Store,
    workspace_id: str,
    org_id: str,
    *,
    user_id: str,
) -> None:
    """Remove a member from a workspace. Raises StoreNotFoundError if absent."""
    ws = await store.get_workspace(workspace_id, org_id)
    if ws is None:
        raise StoreNotFoundError("workspaces", workspace_id)

    removed = await store.remove_workspace_member(workspace_id, user_id)
    if not removed:
        raise StoreNotFoundError("workspace_members", f"{workspace_id}/{user_id}")
