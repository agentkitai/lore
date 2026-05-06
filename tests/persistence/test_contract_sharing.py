"""Contract tests for the SharingOps slice of Store — config, agent, deny, audit, stats, purge, rate.

These tests run against every Store implementation (Phase 1L: Postgres only).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pytest

from lore.persistence import (
    AgentSharingConfigData,
    AuditEventData,
    DenyListRuleData,
    NewAuditEvent,
    NewDenyListRule,
    SharingConfigData,
    SharingConfigPatch,
    Store,
)

# ── helpers ────────────────────────────────────────────────────────────────────


async def _ensure_org(store, org_id: str) -> None:
    """Insert an org row if it doesn't already exist (required by sharing FKs)."""
    await store._conn.execute(
        "INSERT INTO orgs (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        org_id,
        org_id,
    )


async def _insert_lesson(
    store,
    *,
    org_id: str,
    lesson_id: Optional[str] = None,
    reputation_score: int = 0,
    created_at: Optional[datetime] = None,
) -> str:
    """Insert a lesson row via raw SQL."""
    from ulid import ULID

    lesson_id = lesson_id or str(ULID())
    if created_at is None:
        await store._conn.execute(
            """
            INSERT INTO lessons (id, org_id, problem, resolution, reputation_score)
            VALUES ($1, $2, 'p', 'r', $3)
            """,
            lesson_id,
            org_id,
            reputation_score,
        )
    else:
        await store._conn.execute(
            """
            INSERT INTO lessons (id, org_id, problem, resolution, reputation_score, created_at)
            VALUES ($1, $2, 'p', 'r', $3, $4)
            """,
            lesson_id,
            org_id,
            reputation_score,
            created_at,
        )
    return lesson_id


# ── get_or_init_sharing_config ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_init_creates_default_when_missing(store: Store):
    await _ensure_org(store, "org-init")

    cfg = await store.get_or_init_sharing_config("org-init")

    assert isinstance(cfg, SharingConfigData)
    assert cfg.enabled is False
    assert cfg.human_review_enabled is False
    assert cfg.rate_limit_per_hour == 100
    assert cfg.volume_alert_threshold == 1000

    # Row was actually inserted
    row = await store._conn.fetchrow(
        "SELECT enabled FROM sharing_config WHERE org_id = $1", "org-init",
    )
    assert row is not None


@pytest.mark.asyncio
async def test_get_or_init_returns_existing_row(store: Store):
    await _ensure_org(store, "org-existing")
    await store._conn.execute(
        """
        INSERT INTO sharing_config (id, org_id, enabled, human_review_enabled,
                                     rate_limit_per_hour, volume_alert_threshold)
        VALUES ('cfg-1', $1, TRUE, TRUE, 250, 5000)
        """,
        "org-existing",
    )

    cfg = await store.get_or_init_sharing_config("org-existing")

    assert cfg.enabled is True
    assert cfg.human_review_enabled is True
    assert cfg.rate_limit_per_hour == 250
    assert cfg.volume_alert_threshold == 5000


# ── update_sharing_config ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_creates_then_patches(store: Store):
    await _ensure_org(store, "org-upd")

    patch = SharingConfigPatch(enabled=True, rate_limit_per_hour=500)
    cfg = await store.update_sharing_config("org-upd", patch)

    assert cfg.enabled is True
    assert cfg.rate_limit_per_hour == 500
    # untouched fields keep defaults
    assert cfg.human_review_enabled is False
    assert cfg.volume_alert_threshold == 1000


@pytest.mark.asyncio
async def test_update_empty_patch_only_bumps_updated_at(store: Store):
    await _ensure_org(store, "org-empty-upd")
    # Pre-create a row
    await store.update_sharing_config(
        "org-empty-upd", SharingConfigPatch(enabled=True),
    )

    cfg = await store.update_sharing_config(
        "org-empty-upd", SharingConfigPatch(),
    )

    # Existing field preserved (enabled stays True)
    assert cfg.enabled is True
    assert cfg.updated_at is not None


# ── list_agent_sharing_configs ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_agent_configs_returns_org_only_ordered_by_agent_id(store: Store):
    await _ensure_org(store, "org-agents")
    await _ensure_org(store, "other-org")

    await store.upsert_agent_sharing_config(
        "org-agents", "b-agent", enabled=True, categories=["a", "b"],
    )
    await store.upsert_agent_sharing_config(
        "org-agents", "a-agent", enabled=False, categories=[],
    )
    await store.upsert_agent_sharing_config(
        "other-org", "z-agent", enabled=True, categories=[],
    )

    results = await store.list_agent_sharing_configs("org-agents")

    assert len(results) == 2
    assert all(isinstance(r, AgentSharingConfigData) for r in results)
    assert [r.agent_id for r in results] == ["a-agent", "b-agent"]


# ── upsert_agent_sharing_config ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_agent_inserts_new_row(store: Store):
    await _ensure_org(store, "org-upsert")

    cfg = await store.upsert_agent_sharing_config(
        "org-upsert", "agent-x", enabled=True, categories=["lessons"],
    )

    assert cfg.agent_id == "agent-x"
    assert cfg.enabled is True
    assert list(cfg.categories) == ["lessons"]


@pytest.mark.asyncio
async def test_upsert_agent_updates_existing_row(store: Store):
    await _ensure_org(store, "org-upsert-2")
    await store.upsert_agent_sharing_config(
        "org-upsert-2", "agent-y", enabled=False, categories=["a"],
    )

    cfg = await store.upsert_agent_sharing_config(
        "org-upsert-2", "agent-y", enabled=True, categories=["a", "b"],
    )

    assert cfg.enabled is True
    assert list(cfg.categories) == ["a", "b"]


# ── deny-list ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_list_deny_rules(store: Store):
    await _ensure_org(store, "org-deny")

    r1 = await store.create_deny_rule(
        NewDenyListRule(org_id="org-deny", pattern="^secret", is_regex=True, reason="r1"),
    )
    r2 = await store.create_deny_rule(
        NewDenyListRule(org_id="org-deny", pattern="literal-string"),
    )

    assert isinstance(r1, DenyListRuleData)
    assert r1.pattern == "^secret"
    assert r1.is_regex is True
    assert r1.reason == "r1"
    assert r1.id != r2.id
    assert r2.is_regex is False
    assert r2.reason is None

    results = await store.list_deny_rules("org-deny")
    assert len(results) == 2
    patterns = {r.pattern for r in results}
    assert patterns == {"^secret", "literal-string"}


@pytest.mark.asyncio
async def test_list_deny_rules_filters_by_org(store: Store):
    await _ensure_org(store, "org-d-a")
    await _ensure_org(store, "org-d-b")
    await store.create_deny_rule(NewDenyListRule(org_id="org-d-a", pattern="A"))
    await store.create_deny_rule(NewDenyListRule(org_id="org-d-b", pattern="B"))

    results = await store.list_deny_rules("org-d-a")
    assert len(results) == 1
    assert results[0].pattern == "A"


@pytest.mark.asyncio
async def test_delete_deny_rule_removes_row(store: Store):
    await _ensure_org(store, "org-del-rule")
    r = await store.create_deny_rule(NewDenyListRule(org_id="org-del-rule", pattern="x"))

    assert await store.delete_deny_rule(r.id, "org-del-rule") is True

    results = await store.list_deny_rules("org-del-rule")
    assert results == ()


@pytest.mark.asyncio
async def test_delete_deny_rule_missing_returns_false(store: Store):
    await _ensure_org(store, "org-del-missing")

    assert await store.delete_deny_rule("does-not-exist", "org-del-missing") is False


@pytest.mark.asyncio
async def test_delete_deny_rule_wrong_org_returns_false(store: Store):
    await _ensure_org(store, "org-wrong-a")
    await _ensure_org(store, "org-wrong-b")
    r = await store.create_deny_rule(NewDenyListRule(org_id="org-wrong-a", pattern="x"))

    assert await store.delete_deny_rule(r.id, "org-wrong-b") is False
    # Still exists for the original org
    results = await store.list_deny_rules("org-wrong-a")
    assert len(results) == 1


# ── audit events ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_and_list_audit_events(store: Store):
    await _ensure_org(store, "org-audit")

    await store.record_audit_event(
        NewAuditEvent(
            org_id="org-audit",
            event_type="share",
            initiated_by="key-1",
            lesson_id="lesson-1",
            query_text=None,
        ),
    )
    await store.record_audit_event(
        NewAuditEvent(
            org_id="org-audit",
            event_type="purge",
            initiated_by="key-2",
        ),
    )

    results = await store.list_audit_events("org-audit")
    assert len(results) == 2
    assert all(isinstance(r, AuditEventData) for r in results)
    types = {r.event_type for r in results}
    assert types == {"share", "purge"}


@pytest.mark.asyncio
async def test_list_audit_events_filters_by_event_type(store: Store):
    await _ensure_org(store, "org-audit-filter")
    await store.record_audit_event(
        NewAuditEvent(org_id="org-audit-filter", event_type="share", initiated_by="k"),
    )
    await store.record_audit_event(
        NewAuditEvent(org_id="org-audit-filter", event_type="rate", initiated_by="k"),
    )

    results = await store.list_audit_events("org-audit-filter", event_type="rate")
    assert len(results) == 1
    assert results[0].event_type == "rate"


@pytest.mark.asyncio
async def test_list_audit_events_filters_by_date_range(store: Store):
    from datetime import timedelta, timezone

    await _ensure_org(store, "org-audit-date")
    # Insert one with explicit created_at via raw SQL
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    recent = now - timedelta(hours=1)

    from ulid import ULID

    await store._conn.execute(
        """
        INSERT INTO sharing_audit (id, org_id, event_type, initiated_by, created_at)
        VALUES ($1, $2, 'share', 'k', $3)
        """,
        str(ULID()),
        "org-audit-date",
        old,
    )
    await store._conn.execute(
        """
        INSERT INTO sharing_audit (id, org_id, event_type, initiated_by, created_at)
        VALUES ($1, $2, 'share', 'k', $3)
        """,
        str(ULID()),
        "org-audit-date",
        recent,
    )

    results = await store.list_audit_events(
        "org-audit-date", from_date=now - timedelta(days=1),
    )
    assert len(results) == 1


@pytest.mark.asyncio
async def test_list_audit_events_respects_limit_and_org(store: Store):
    await _ensure_org(store, "org-limit-a")
    await _ensure_org(store, "org-limit-b")
    for _ in range(3):
        await store.record_audit_event(
            NewAuditEvent(org_id="org-limit-a", event_type="share", initiated_by="k"),
        )
    await store.record_audit_event(
        NewAuditEvent(org_id="org-limit-b", event_type="share", initiated_by="k"),
    )

    results = await store.list_audit_events("org-limit-a", limit=2)
    assert len(results) == 2
