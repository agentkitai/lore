"""Sharing service — config CRUD, agent overrides, deny rules, audit, stats, purge, rate."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Sequence

from lore.persistence import (
    AgentSharingConfigData,
    AuditEventData,
    DenyListRuleData,
    NewAuditEvent,
    NewDenyListRule,
    SharingConfigData,
    SharingConfigPatch,
    SharingStatsData,
    Store,
)

logger = logging.getLogger(__name__)


# ── config ────────────────────────────────────────────────────────────────────


async def get_or_init_config(store: Store, *, org_id: str) -> SharingConfigData:
    """Return the org's sharing config, lazily creating defaults on first read."""
    return await store.get_or_init_sharing_config(org_id)


async def update_config(
    store: Store,
    *,
    org_id: str,
    patch: SharingConfigPatch,
) -> SharingConfigData:
    """Upsert + patch the org's sharing config; returns the updated row."""
    return await store.update_sharing_config(org_id, patch)


# ── agent configs ─────────────────────────────────────────────────────────────


async def list_agent_configs(
    store: Store, *, org_id: str,
) -> Sequence[AgentSharingConfigData]:
    """List per-agent sharing configs for an org, ordered by agent_id."""
    return await store.list_agent_sharing_configs(org_id)


async def upsert_agent_config(
    store: Store,
    *,
    org_id: str,
    agent_id: str,
    enabled: Optional[bool],
    categories: Optional[Sequence[str]],
) -> AgentSharingConfigData:
    """Insert or update the sharing config for a (org, agent) pair.

    Mirrors the legacy route shape: ``enabled`` falls back to False when None,
    and ``categories`` falls back to an empty list. Both come from the
    PUT /v1/sharing/agents/{agent_id} body which accepts optional fields.
    """
    enabled_resolved = enabled if enabled is not None else False
    categories_resolved = list(categories) if categories is not None else []
    return await store.upsert_agent_sharing_config(
        org_id,
        agent_id,
        enabled=enabled_resolved,
        categories=categories_resolved,
    )


# ── deny-list ─────────────────────────────────────────────────────────────────


async def list_deny_rules(
    store: Store, *, org_id: str,
) -> Sequence[DenyListRuleData]:
    """List deny-list rules for an org."""
    return await store.list_deny_rules(org_id)


async def create_deny_rule(
    store: Store,
    *,
    org_id: str,
    pattern: str,
    is_regex: bool = False,
    reason: Optional[str] = None,
) -> DenyListRuleData:
    """Create a deny-list rule for an org."""
    rule = NewDenyListRule(
        org_id=org_id,
        pattern=pattern,
        is_regex=is_regex,
        reason=reason,
    )
    return await store.create_deny_rule(rule)


async def delete_deny_rule(
    store: Store, *, rule_id: str, org_id: str,
) -> bool:
    """Delete a deny-list rule scoped to an org; returns True if removed."""
    return await store.delete_deny_rule(rule_id, org_id)


# ── audit ─────────────────────────────────────────────────────────────────────


async def list_audit_events(
    store: Store,
    *,
    org_id: str,
    event_type: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    limit: int = 50,
) -> Sequence[AuditEventData]:
    """List sharing audit events for an org with optional filters; newest first."""
    return await store.list_audit_events(
        org_id,
        event_type=event_type,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
    )


async def record_audit_event(
    store: Store,
    *,
    org_id: str,
    event_type: str,
    initiated_by: str,
    lesson_id: Optional[str] = None,
    query_text: Optional[str] = None,
) -> None:
    """Persist a sharing audit event row. Wraps NewAuditEvent for the route layer."""
    await store.record_audit_event(
        NewAuditEvent(
            org_id=org_id,
            event_type=event_type,
            initiated_by=initiated_by,
            lesson_id=lesson_id,
            query_text=query_text,
        ),
    )


# ── stats / purge / rate ──────────────────────────────────────────────────────


async def get_stats(store: Store, *, org_id: str) -> SharingStatsData:
    """Aggregate sharing stats for an org."""
    return await store.get_sharing_stats(org_id)


async def purge(
    store: Store,
    *,
    org_id: str,
    confirmation: str,
    initiated_by: str,
) -> int:
    """Purge all sharing-related rows for an org.

    Validates the confirmation string and then writes a 'purge' audit event
    after the cascade succeeds. Returns the deleted-lessons count.
    """
    if confirmation != "PURGE":
        raise ValueError("Confirmation must be 'PURGE'")
    deleted = await store.purge_sharing(org_id)
    await store.record_audit_event(
        NewAuditEvent(
            org_id=org_id,
            event_type="purge",
            initiated_by=initiated_by,
        ),
    )
    return deleted


async def rate_lesson(
    store: Store,
    *,
    lesson_id: str,
    org_id: str,
    delta: int,
    initiated_by: str,
) -> Optional[int]:
    """Rate a lesson +1/-1 atomically.

    Validates ``delta in (1, -1)`` defensively — the route's pydantic model
    already enforces this, but the service stays correct on its own.
    Returns the new reputation_score, or None if the lesson does not exist.
    """
    if delta not in (1, -1):
        raise ValueError("delta must be 1 or -1")
    return await store.rate_lesson(lesson_id, org_id, delta, initiated_by)
