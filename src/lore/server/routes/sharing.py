"""Sharing & community endpoints for Lore Cloud Server."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
    from pydantic import BaseModel
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.persistence import (
    AgentSharingConfigData,
    AuditEventData,
    DenyListRuleData,
    SharingConfigData,
    SharingConfigPatch,
    SharingStatsData,
    Store,
)
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.services import sharing as sharing_service

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
    lesson_id: Optional[str] = None
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


# ── Translation helpers ────────────────────────────────────────────


def _to_config(d: SharingConfigData) -> SharingConfig:
    return SharingConfig(
        enabled=d.enabled,
        human_review_enabled=d.human_review_enabled,
        rate_limit_per_hour=d.rate_limit_per_hour,
        volume_alert_threshold=d.volume_alert_threshold,
        updated_at=d.updated_at,
    )


def _to_agent_config(d: AgentSharingConfigData) -> AgentSharingConfig:
    return AgentSharingConfig(
        agent_id=d.agent_id,
        enabled=d.enabled,
        categories=list(d.categories),
        updated_at=d.updated_at,
    )


def _to_deny_rule(d: DenyListRuleData) -> DenyListRule:
    return DenyListRule(
        id=d.id,
        pattern=d.pattern,
        is_regex=d.is_regex,
        reason=d.reason,
        created_at=d.created_at,
    )


def _to_audit_event(d: AuditEventData) -> AuditEvent:
    return AuditEvent(
        id=d.id,
        event_type=d.event_type,
        lesson_id=d.lesson_id,
        query_text=d.query_text,
        initiated_by=d.initiated_by,
        created_at=d.created_at,
    )


def _to_stats(d: SharingStatsData) -> SharingStats:
    return SharingStats(
        countShared=d.count_shared,
        lastShared=d.last_shared,
        auditSummary=dict(d.audit_summary),
    )


# ── Config ─────────────────────────────────────────────────────────


@router.get("/config", response_model=SharingConfig)
async def get_sharing_config(
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> SharingConfig:
    cfg = await sharing_service.get_or_init_config(store, org_id=auth.org_id)
    return _to_config(cfg)


@router.put("/config", response_model=SharingConfig)
async def update_sharing_config(
    body: SharingConfigUpdate,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> SharingConfig:
    patch = SharingConfigPatch(
        enabled=body.enabled,
        human_review_enabled=body.human_review_enabled,
        rate_limit_per_hour=body.rate_limit_per_hour,
        volume_alert_threshold=body.volume_alert_threshold,
    )
    cfg = await sharing_service.update_config(store, org_id=auth.org_id, patch=patch)
    return _to_config(cfg)


# ── Agent Configs ──────────────────────────────────────────────────


@router.get("/agents", response_model=List[AgentSharingConfig])
async def list_agent_configs(
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[AgentSharingConfig]:
    rows = await sharing_service.list_agent_configs(store, org_id=auth.org_id)
    return [_to_agent_config(r) for r in rows]


@router.put("/agents/{agent_id}", response_model=AgentSharingConfig)
async def upsert_agent_config(
    agent_id: str,
    body: AgentSharingConfigUpdate,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> AgentSharingConfig:
    cfg = await sharing_service.upsert_agent_config(
        store,
        org_id=auth.org_id,
        agent_id=agent_id,
        enabled=body.enabled,
        categories=body.categories,
    )
    return _to_agent_config(cfg)


# ── Deny List ──────────────────────────────────────────────────────


@router.get("/deny-list", response_model=List[DenyListRule])
async def list_deny_rules(
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[DenyListRule]:
    rows = await sharing_service.list_deny_rules(store, org_id=auth.org_id)
    return [_to_deny_rule(r) for r in rows]


@router.post("/deny-list", response_model=DenyListRule, status_code=201)
async def create_deny_rule(
    body: DenyListRuleCreate,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> DenyListRule:
    rule = await sharing_service.create_deny_rule(
        store,
        org_id=auth.org_id,
        pattern=body.pattern,
        is_regex=body.is_regex,
        reason=body.reason,
    )
    return _to_deny_rule(rule)


@router.delete("/deny-list/{rule_id}", status_code=204)
async def delete_deny_rule(
    rule_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> None:
    """Remove a deny rule scoped to the caller's org."""
    deleted = await sharing_service.delete_deny_rule(
        store, rule_id=rule_id, org_id=auth.org_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Rule not found")


# ── Audit ──────────────────────────────────────────────────────────


@router.get("/audit", response_model=List[AuditEvent])
async def list_audit_events(
    event_type: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=500),
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[AuditEvent]:
    rows = await sharing_service.list_audit_events(
        store,
        org_id=auth.org_id,
        event_type=event_type,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
    )
    return [_to_audit_event(r) for r in rows]


# ── Stats ──────────────────────────────────────────────────────────


@router.get("/stats", response_model=SharingStats)
async def get_stats(
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> SharingStats:
    stats = await sharing_service.get_stats(store, org_id=auth.org_id)
    return _to_stats(stats)


# ── Purge ──────────────────────────────────────────────────────────


@router.post("/purge", status_code=200)
async def purge_sharing(
    body: PurgeRequest,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> dict:
    try:
        deleted_lessons = await sharing_service.purge(
            store,
            org_id=auth.org_id,
            confirmation=body.confirmation,
            initiated_by=auth.key_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"deleted_lessons": deleted_lessons, "status": "purged"}


# ── Rate (mounted on lessons prefix) ──────────────────────────────

rate_router = APIRouter(prefix="/v1/lessons", tags=["lessons"])


@rate_router.post("/{lesson_id}/rate", response_model=RateResponse)
async def rate_lesson(
    lesson_id: str,
    body: RateRequest,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> RateResponse:
    try:
        score = await sharing_service.rate_lesson(
            store,
            lesson_id=lesson_id,
            org_id=auth.org_id,
            delta=body.delta,
            initiated_by=auth.key_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if score is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return RateResponse(reputation_score=score)
