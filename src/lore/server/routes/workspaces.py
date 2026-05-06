"""Workspace management endpoints — /v1/workspaces."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Response
except ImportError:
    raise ImportError("FastAPI is required.")

from pydantic import BaseModel

from lore.persistence import Store, StoredMember, StoredWorkspace, WorkspacePatch
from lore.persistence.exceptions import IntegrityError, StoreNotFoundError
from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_store
from lore.services import workspaces as workspaces_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])


# ── Request / response models ─────────────────────────────────────


class WorkspaceCreateRequest(BaseModel):
    name: str
    slug: str
    settings: Dict[str, Any] = {}


class WorkspaceUpdateRequest(BaseModel):
    name: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class WorkspaceResponse(BaseModel):
    id: str
    org_id: str
    name: str
    slug: str
    settings: Dict[str, Any] = {}
    created_at: Optional[str] = None
    archived_at: Optional[str] = None


class MemberAddRequest(BaseModel):
    user_id: str
    role: str = "member"


class MemberRoleUpdateRequest(BaseModel):
    role: str = "member"


class MemberResponse(BaseModel):
    id: str
    workspace_id: str
    user_id: Optional[str] = None
    role: str
    invited_at: Optional[str] = None
    accepted_at: Optional[str] = None


# ── Serialisation helpers ─────────────────────────────────────────


def _to_workspace_response(w: StoredWorkspace) -> WorkspaceResponse:
    return WorkspaceResponse(
        id=w.id,
        org_id=w.org_id,
        name=w.name,
        slug=w.slug,
        settings=dict(w.settings),
        created_at=w.created_at.isoformat() if w.created_at else None,
        archived_at=w.archived_at.isoformat() if w.archived_at else None,
    )


def _to_member_response(m: StoredMember) -> MemberResponse:
    return MemberResponse(
        id=m.id,
        workspace_id=m.workspace_id,
        user_id=m.user_id,
        role=m.role,
        invited_at=m.invited_at.isoformat() if m.invited_at else None,
        accepted_at=m.accepted_at.isoformat() if m.accepted_at else None,
    )


def _patch_from_update_body(body: WorkspaceUpdateRequest) -> WorkspacePatch:
    return WorkspacePatch(name=body.name, settings=body.settings)


# ── Handlers ──────────────────────────────────────────────────────


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    body: WorkspaceCreateRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> WorkspaceResponse:
    try:
        ws = await workspaces_service.create_workspace(
            store,
            org_id=auth.org_id,
            name=body.name,
            slug=body.slug,
            settings=body.settings,
        )
    except IntegrityError:
        raise HTTPException(409, f"Workspace slug '{body.slug}' already exists")
    return _to_workspace_response(ws)


@router.get("", response_model=List[WorkspaceResponse])
async def list_workspaces(
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[WorkspaceResponse]:
    workspaces = await workspaces_service.list_workspaces(store, auth.org_id)
    return [_to_workspace_response(w) for w in workspaces]


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> WorkspaceResponse:
    try:
        ws = await workspaces_service.get_workspace(store, workspace_id, auth.org_id)
    except StoreNotFoundError:
        raise HTTPException(404, "Workspace not found")
    return _to_workspace_response(ws)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdateRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> WorkspaceResponse:
    patch = _patch_from_update_body(body)
    try:
        ws = await workspaces_service.update_workspace(store, workspace_id, auth.org_id, patch)
    except StoreNotFoundError:
        raise HTTPException(404, "Workspace not found")
    except ValueError as exc:
        if "empty patch" in str(exc).lower():
            raise HTTPException(400, str(exc))
        raise
    return _to_workspace_response(ws)


@router.put("/{workspace_id}", response_model=WorkspaceResponse)
async def replace_workspace(
    workspace_id: str,
    body: WorkspaceUpdateRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> WorkspaceResponse:
    """Full update of workspace fields (name and settings)."""
    try:
        ws = await workspaces_service.replace_workspace(
            store,
            workspace_id,
            auth.org_id,
            name=body.name,
            settings=body.settings,
        )
    except StoreNotFoundError:
        raise HTTPException(404, "Workspace not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _to_workspace_response(ws)


@router.delete("/{workspace_id}", status_code=204)
async def archive_workspace(
    workspace_id: str,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> Response:
    try:
        await workspaces_service.archive_workspace(store, workspace_id, auth.org_id)
    except StoreNotFoundError:
        raise HTTPException(404, "Workspace not found")
    return Response(status_code=204)


@router.post("/{workspace_id}/members", response_model=MemberResponse, status_code=201)
async def add_member(
    workspace_id: str,
    body: MemberAddRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> MemberResponse:
    try:
        member = await workspaces_service.add_member(
            store,
            workspace_id,
            auth.org_id,
            user_id=body.user_id,
            role=body.role,
        )
    except StoreNotFoundError:
        raise HTTPException(404, "Workspace not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except IntegrityError:
        raise HTTPException(409, "Member already exists")
    return _to_member_response(member)


@router.get("/{workspace_id}/members", response_model=List[MemberResponse])
async def list_members(
    workspace_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[MemberResponse]:
    try:
        members = await workspaces_service.list_members(store, workspace_id, auth.org_id)
    except StoreNotFoundError:
        raise HTTPException(404, "Workspace not found")
    return [_to_member_response(m) for m in members]


@router.patch("/{workspace_id}/members/{user_id}")
async def update_member_role(
    workspace_id: str,
    user_id: str,
    body: MemberRoleUpdateRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> MemberResponse:
    try:
        member = await workspaces_service.update_member_role(
            store,
            workspace_id,
            auth.org_id,
            user_id=user_id,
            role=body.role,
        )
    except StoreNotFoundError:
        raise HTTPException(404, "Member not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _to_member_response(member)


@router.delete("/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(
    workspace_id: str,
    user_id: str,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> Response:
    try:
        await workspaces_service.remove_member(
            store,
            workspace_id,
            auth.org_id,
            user_id=user_id,
        )
    except StoreNotFoundError:
        raise HTTPException(404, "Member not found")
    return Response(status_code=204)
