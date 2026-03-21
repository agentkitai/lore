"""Workspace management endpoints — /v1/workspaces."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException
except ImportError:
    raise ImportError("FastAPI is required.")

from pydantic import BaseModel

from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])


class WorkspaceCreateRequest(BaseModel):
    name: str
    slug: str
    settings: Dict[str, Any] = {}


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
    role: str = "writer"


class MemberResponse(BaseModel):
    id: str
    workspace_id: str
    user_id: Optional[str] = None
    role: str
    invited_at: Optional[str] = None
    accepted_at: Optional[str] = None


def _ts(val) -> Optional[str]:
    if val is None:
        return None
    from datetime import datetime
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    body: WorkspaceCreateRequest,
    auth: AuthContext = Depends(require_role("admin")),
) -> WorkspaceResponse:
    from ulid import ULID
    ws_id = str(ULID())
    import json
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """INSERT INTO workspaces (id, org_id, name, slug, settings)
                   VALUES ($1, $2, $3, $4, $5::jsonb) RETURNING *""",
                ws_id, auth.org_id, body.name, body.slug,
                json.dumps(body.settings),
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(409, f"Workspace slug '{body.slug}' already exists")
            raise
    return WorkspaceResponse(
        id=row["id"], org_id=row["org_id"], name=row["name"],
        slug=row["slug"], settings=row["settings"] or {},
        created_at=_ts(row["created_at"]),
    )


@router.get("", response_model=List[WorkspaceResponse])
async def list_workspaces(
    auth: AuthContext = Depends(get_auth_context),
) -> List[WorkspaceResponse]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM workspaces WHERE org_id = $1 AND archived_at IS NULL ORDER BY name",
            auth.org_id,
        )
    return [
        WorkspaceResponse(
            id=r["id"], org_id=r["org_id"], name=r["name"],
            slug=r["slug"], settings=r["settings"] or {},
            created_at=_ts(r["created_at"]),
        )
        for r in rows
    ]


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> WorkspaceResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM workspaces WHERE id = $1 AND org_id = $2",
            workspace_id, auth.org_id,
        )
    if not row:
        raise HTTPException(404, "Workspace not found")
    return WorkspaceResponse(
        id=row["id"], org_id=row["org_id"], name=row["name"],
        slug=row["slug"], settings=row["settings"] or {},
        created_at=_ts(row["created_at"]),
        archived_at=_ts(row["archived_at"]),
    )


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    body: Dict[str, Any],
    auth: AuthContext = Depends(require_role("admin")),
) -> WorkspaceResponse:
    import json
    pool = await get_pool()
    async with pool.acquire() as conn:
        updates = []
        params: list = [workspace_id, auth.org_id]
        if "name" in body:
            params.append(body["name"])
            updates.append(f"name = ${len(params)}")
        if "settings" in body:
            params.append(json.dumps(body["settings"]))
            updates.append(f"settings = ${len(params)}::jsonb")
        if not updates:
            raise HTTPException(400, "No fields to update")
        row = await conn.fetchrow(
            f"UPDATE workspaces SET {', '.join(updates)} WHERE id = $1 AND org_id = $2 RETURNING *",
            *params,
        )
    if not row:
        raise HTTPException(404, "Workspace not found")
    return WorkspaceResponse(
        id=row["id"], org_id=row["org_id"], name=row["name"],
        slug=row["slug"], settings=row["settings"] or {},
        created_at=_ts(row["created_at"]),
    )


@router.delete("/{workspace_id}", status_code=204)
async def archive_workspace(
    workspace_id: str,
    auth: AuthContext = Depends(require_role("admin")),
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE workspaces SET archived_at = now() WHERE id = $1 AND org_id = $2",
            workspace_id, auth.org_id,
        )
        if result == "UPDATE 0":
            raise HTTPException(404, "Workspace not found")


@router.post("/{workspace_id}/members", response_model=MemberResponse, status_code=201)
async def add_member(
    workspace_id: str,
    body: MemberAddRequest,
    auth: AuthContext = Depends(require_role("admin")),
) -> MemberResponse:
    from ulid import ULID
    member_id = str(ULID())
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO workspace_members (id, workspace_id, user_id, role)
               VALUES ($1, $2, $3, $4) RETURNING *""",
            member_id, workspace_id, body.user_id, body.role,
        )
    return MemberResponse(
        id=row["id"], workspace_id=row["workspace_id"],
        user_id=row["user_id"], role=row["role"],
        invited_at=_ts(row["invited_at"]),
    )


@router.get("/{workspace_id}/members", response_model=List[MemberResponse])
async def list_members(
    workspace_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> List[MemberResponse]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM workspace_members WHERE workspace_id = $1",
            workspace_id,
        )
    return [
        MemberResponse(
            id=r["id"], workspace_id=r["workspace_id"],
            user_id=r["user_id"], role=r["role"],
            invited_at=_ts(r["invited_at"]),
            accepted_at=_ts(r["accepted_at"]),
        )
        for r in rows
    ]


@router.patch("/{workspace_id}/members/{user_id}")
async def update_member_role(
    workspace_id: str,
    user_id: str,
    body: Dict[str, str],
    auth: AuthContext = Depends(require_role("admin")),
) -> Dict[str, str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE workspace_members SET role = $1 WHERE workspace_id = $2 AND user_id = $3",
            body.get("role", "writer"), workspace_id, user_id,
        )
        if result == "UPDATE 0":
            raise HTTPException(404, "Member not found")
    return {"status": "updated"}


@router.delete("/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(
    workspace_id: str,
    user_id: str,
    auth: AuthContext = Depends(require_role("admin")),
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM workspace_members WHERE workspace_id = $1 AND user_id = $2",
            workspace_id, user_id,
        )
        if result == "DELETE 0":
            raise HTTPException(404, "Member not found")
