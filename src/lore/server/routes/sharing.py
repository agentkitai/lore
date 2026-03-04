"""Sharing & community endpoints for Lore Cloud Server."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
    from pydantic import BaseModel
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

try:
    from ulid import ULID
except ImportError:
    raise ImportError("python-ulid is required. Install with: pip install python-ulid")

from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/sharing", tags=["sharing"])


# ── Models ─────────────────────────────────────────────────────────


class SharingConfig(BaseModel):
    enabled: bool = False
    human_review_enabled: bool = False
    rate_limit_per_hour: int = 100
    volume_alert_threshold: int = 1000
    updated_at: Optional[datetime] = None


class SharingConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    human_review_enabled: Optional[bool] = None
    rate_limit_per_hour: Optional[int] = None
    volume_alert_threshold: Optional[int] = None


class AgentSharingConfig(BaseModel):
    agent_id: str
    enabled: bool = False
    categories: List[str] = []
    updated_at: Optional[datetime] = None


class AgentSharingConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    categories: Optional[List[str]] = None


class DenyListRule(BaseModel):
    id: str
    pattern: str
    is_regex: bool = False
    reason: Optional[str] = None
    created_at: Optional[datetime] = None


class DenyListRuleCreate(BaseModel):
    pattern: str
    is_regex: bool = False
    reason: Optional[str] = None


class AuditEvent(BaseModel):
    id: str
    event_type: str
    memory_id: Optional[str] = None
    query_text: Optional[str] = None
    initiated_by: str
    created_at: Optional[datetime] = None


class SharingStats(BaseModel):
    countShared: int
    lastShared: Optional[datetime] = None
    auditSummary: Dict[str, int] = {}


class RateRequest(BaseModel):
    delta: int  # 1 or -1

    def model_post_init(self, __context: Any) -> None:
        if self.delta not in (1, -1):
            raise ValueError("delta must be 1 or -1")


class RateResponse(BaseModel):
    reputation_score: int


class PurgeRequest(BaseModel):
    confirmation: str


# ── Helpers ────────────────────────────────────────────────────────


async def _record_audit(
    org_id: str,
    event_type: str,
    initiated_by: str,
    memory_id: Optional[str] = None,
    query_text: Optional[str] = None,
) -> None:
    """Insert an audit event."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO sharing_audit (id, org_id, event_type, memory_id, query_text, initiated_by)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            str(ULID()),
            org_id,
            event_type,
            memory_id,
            query_text,
            initiated_by,
        )


# ── Config ─────────────────────────────────────────────────────────


@router.get("/config", response_model=SharingConfig)
async def get_sharing_config(
    auth: AuthContext = Depends(get_auth_context),
) -> SharingConfig:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled, human_review_enabled, rate_limit_per_hour, volume_alert_threshold, updated_at FROM sharing_config WHERE org_id = $1",
            auth.org_id,
        )
        if row is None:
            # Create default
            cfg_id = str(ULID())
            await conn.execute(
                "INSERT INTO sharing_config (id, org_id) VALUES ($1, $2) ON CONFLICT (org_id) DO NOTHING",
                cfg_id,
                auth.org_id,
            )
            return SharingConfig()
    return SharingConfig(**dict(row))


@router.put("/config", response_model=SharingConfig)
async def update_sharing_config(
    body: SharingConfigUpdate,
    auth: AuthContext = Depends(get_auth_context),
) -> SharingConfig:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Ensure row exists
        existing = await conn.fetchval("SELECT id FROM sharing_config WHERE org_id = $1", auth.org_id)
        if existing is None:
            await conn.execute(
                "INSERT INTO sharing_config (id, org_id) VALUES ($1, $2)",
                str(ULID()),
                auth.org_id,
            )

        set_parts = ["updated_at = now()"]
        params: list = [auth.org_id]
        for field in ("enabled", "human_review_enabled", "rate_limit_per_hour", "volume_alert_threshold"):
            val = getattr(body, field)
            if val is not None:
                params.append(val)
                set_parts.append(f"{field} = ${len(params)}")

        row = await conn.fetchrow(
            f"UPDATE sharing_config SET {', '.join(set_parts)} WHERE org_id = $1 RETURNING enabled, human_review_enabled, rate_limit_per_hour, volume_alert_threshold, updated_at",
            *params,
        )
    return SharingConfig(**dict(row))


# ── Agent Configs ──────────────────────────────────────────────────


@router.get("/agents", response_model=List[AgentSharingConfig])
async def list_agent_configs(
    auth: AuthContext = Depends(get_auth_context),
) -> List[AgentSharingConfig]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT agent_id, enabled, categories, updated_at FROM agent_sharing_config WHERE org_id = $1 ORDER BY agent_id",
            auth.org_id,
        )
    results = []
    for r in rows:
        rd = dict(r)
        cats = rd.get("categories") or []
        if isinstance(cats, str):
            cats = json.loads(cats)
        results.append(AgentSharingConfig(agent_id=rd["agent_id"], enabled=rd["enabled"], categories=cats, updated_at=rd["updated_at"]))
    return results


@router.put("/agents/{agent_id}", response_model=AgentSharingConfig)
async def upsert_agent_config(
    agent_id: str,
    body: AgentSharingConfigUpdate,
    auth: AuthContext = Depends(get_auth_context),
) -> AgentSharingConfig:
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    enabled = body.enabled if body.enabled is not None else False
    categories = json.dumps(body.categories) if body.categories is not None else "[]"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO agent_sharing_config (id, org_id, agent_id, enabled, categories, updated_at)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6)
               ON CONFLICT (org_id, agent_id) DO UPDATE SET
                   enabled = COALESCE($4, agent_sharing_config.enabled),
                   categories = COALESCE($5::jsonb, agent_sharing_config.categories),
                   updated_at = $6
               RETURNING agent_id, enabled, categories, updated_at""",
            str(ULID()),
            auth.org_id,
            agent_id,
            enabled,
            categories,
            now,
        )
    rd = dict(row)
    cats = rd.get("categories") or []
    if isinstance(cats, str):
        cats = json.loads(cats)
    return AgentSharingConfig(agent_id=rd["agent_id"], enabled=rd["enabled"], categories=cats, updated_at=rd["updated_at"])


# ── Deny List ──────────────────────────────────────────────────────


@router.get("/deny-list", response_model=List[DenyListRule])
async def list_deny_rules(
    auth: AuthContext = Depends(get_auth_context),
) -> List[DenyListRule]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, pattern, is_regex, reason, created_at FROM deny_list_rules WHERE org_id = $1 ORDER BY created_at",
            auth.org_id,
        )
    return [DenyListRule(**dict(r)) for r in rows]


@router.post("/deny-list", response_model=DenyListRule, status_code=201)
async def create_deny_rule(
    body: DenyListRuleCreate,
    auth: AuthContext = Depends(get_auth_context),
) -> DenyListRule:
    rule_id = str(ULID())
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO deny_list_rules (id, org_id, pattern, is_regex, reason)
               VALUES ($1, $2, $3, $4, $5)
               RETURNING id, pattern, is_regex, reason, created_at""",
            rule_id,
            auth.org_id,
            body.pattern,
            body.is_regex,
            body.reason,
        )
    return DenyListRule(**dict(row))


@router.delete("/deny-list/{rule_id}", status_code=204)
async def delete_deny_rule(
    rule_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM deny_list_rules WHERE id = $1 AND org_id = $2",
            rule_id,
            auth.org_id,
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Rule not found")


# ── Audit ──────────────────────────────────────────────────────────


@router.get("/audit", response_model=List[AuditEvent])
async def list_audit_events(
    event_type: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=500),
    auth: AuthContext = Depends(get_auth_context),
) -> List[AuditEvent]:
    where = ["org_id = $1"]
    params: list = [auth.org_id]

    if event_type:
        params.append(event_type)
        where.append(f"event_type = ${len(params)}")
    if from_date:
        params.append(from_date)
        where.append(f"created_at >= ${len(params)}")
    if to_date:
        params.append(to_date)
        where.append(f"created_at <= ${len(params)}")

    params.append(limit)
    limit_idx = len(params)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT id, event_type, memory_id, query_text, initiated_by, created_at FROM sharing_audit WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ${limit_idx}",
            *params,
        )
    return [AuditEvent(**dict(r)) for r in rows]


# ── Stats ──────────────────────────────────────────────────────────


@router.get("/stats", response_model=SharingStats)
async def get_stats(
    auth: AuthContext = Depends(get_auth_context),
) -> SharingStats:
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE org_id = $1", auth.org_id)
        last = await conn.fetchval("SELECT MAX(created_at) FROM memories WHERE org_id = $1", auth.org_id)
        summary_rows = await conn.fetch(
            "SELECT event_type, COUNT(*)::int as cnt FROM sharing_audit WHERE org_id = $1 GROUP BY event_type",
            auth.org_id,
        )
    summary = {r["event_type"]: r["cnt"] for r in summary_rows}
    return SharingStats(countShared=count or 0, lastShared=last, auditSummary=summary)


# ── Purge ──────────────────────────────────────────────────────────


@router.post("/purge", status_code=200)
async def purge_sharing(
    body: PurgeRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> dict:
    if body.confirmation != "PURGE":
        raise HTTPException(status_code=400, detail="Confirmation must be 'PURGE'")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted_memories = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE org_id = $1", auth.org_id)
            await conn.execute("DELETE FROM memories WHERE org_id = $1", auth.org_id)
            await conn.execute("DELETE FROM sharing_audit WHERE org_id = $1", auth.org_id)
            await conn.execute("DELETE FROM deny_list_rules WHERE org_id = $1", auth.org_id)
            await conn.execute("DELETE FROM agent_sharing_config WHERE org_id = $1", auth.org_id)
            await conn.execute("DELETE FROM sharing_config WHERE org_id = $1", auth.org_id)

    await _record_audit(auth.org_id, "purge", auth.key_id)
    return {"deleted_memories": deleted_memories, "status": "purged"}


# ── Rate (mounted on memories prefix) ─────────────────────────────

rate_router = APIRouter(prefix="/v1/memories", tags=["memories"])


@rate_router.post("/{memory_id}/rate", response_model=RateResponse)
async def rate_memory(
    memory_id: str,
    body: RateRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> RateResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE memories SET reputation_score = reputation_score + $1, updated_at = now() WHERE id = $2 AND org_id = $3 RETURNING reputation_score",
                body.delta,
                memory_id,
                auth.org_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="Memory not found")
            await conn.execute(
                """INSERT INTO sharing_audit (id, org_id, event_type, memory_id, initiated_by)
                   VALUES ($1, $2, $3, $4, $5)""",
                str(ULID()),
                auth.org_id,
                "rate",
                memory_id,
                auth.key_id,
            )
    return RateResponse(reputation_score=row["reputation_score"])
