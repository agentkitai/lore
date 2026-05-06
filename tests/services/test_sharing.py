"""Service-level tests for lore.services.sharing.

Uses a real Postgres store (via conftest fixture) for integration tests.
"""

from __future__ import annotations

from typing import Optional

import pytest

from lore.persistence import SharingConfigPatch
from lore.services import sharing

# ── helpers ───────────────────────────────────────────────────────────────────


async def _ensure_org(store, org_id: str) -> None:
    await store._conn.execute(
        "INSERT INTO orgs (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        org_id,
        org_id,
    )


async def _insert_lesson(
    store, *, org_id: str, lesson_id: Optional[str] = None, reputation_score: int = 0,
) -> str:
    from ulid import ULID

    lesson_id = lesson_id or str(ULID())
    await store._conn.execute(
        """
        INSERT INTO memories (id, org_id, content, context, reputation_score)
        VALUES ($1, $2, 'p', 'r', $3)
        """,
        lesson_id,
        org_id,
        reputation_score,
    )
    return lesson_id


# ── config ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_init_config_creates_default(store):
    await _ensure_org(store, "svc-cfg-1")

    cfg = await sharing.get_or_init_config(store, org_id="svc-cfg-1")

    assert cfg.enabled is False
    assert cfg.rate_limit_per_hour == 100


@pytest.mark.asyncio
async def test_update_config_applies_patch(store):
    await _ensure_org(store, "svc-cfg-2")
    patch = SharingConfigPatch(enabled=True, volume_alert_threshold=2500)

    cfg = await sharing.update_config(store, org_id="svc-cfg-2", patch=patch)

    assert cfg.enabled is True
    assert cfg.volume_alert_threshold == 2500


# ── agent configs ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_agent_config_defaults_when_none(store):
    """When ``enabled`` is None, service falls back to False; categories → []."""
    await _ensure_org(store, "svc-agent-1")

    cfg = await sharing.upsert_agent_config(
        store,
        org_id="svc-agent-1",
        agent_id="agent-x",
        enabled=None,
        categories=None,
    )

    assert cfg.agent_id == "agent-x"
    assert cfg.enabled is False
    assert list(cfg.categories) == []


@pytest.mark.asyncio
async def test_list_agent_configs_passthrough(store):
    await _ensure_org(store, "svc-agent-list")
    await sharing.upsert_agent_config(
        store, org_id="svc-agent-list", agent_id="a1", enabled=True, categories=["x"],
    )
    await sharing.upsert_agent_config(
        store, org_id="svc-agent-list", agent_id="a2", enabled=False, categories=[],
    )

    results = await sharing.list_agent_configs(store, org_id="svc-agent-list")
    assert len(results) == 2


# ── deny-list ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_list_deny_rules(store):
    await _ensure_org(store, "svc-deny")

    r = await sharing.create_deny_rule(
        store, org_id="svc-deny", pattern="^secret", is_regex=True, reason="r",
    )
    assert r.is_regex is True

    rules = await sharing.list_deny_rules(store, org_id="svc-deny")
    assert len(rules) == 1


@pytest.mark.asyncio
async def test_delete_deny_rule_returns_bool(store):
    await _ensure_org(store, "svc-deny-del")
    r = await sharing.create_deny_rule(
        store, org_id="svc-deny-del", pattern="x",
    )

    assert await sharing.delete_deny_rule(
        store, rule_id=r.id, org_id="svc-deny-del",
    ) is True
    assert await sharing.delete_deny_rule(
        store, rule_id=r.id, org_id="svc-deny-del",
    ) is False


# ── audit ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_and_list_audit_events(store):
    await _ensure_org(store, "svc-audit")

    await sharing.record_audit_event(
        store, org_id="svc-audit", event_type="share", initiated_by="k1",
    )
    await sharing.record_audit_event(
        store, org_id="svc-audit", event_type="rate", initiated_by="k1",
        lesson_id="lesson-1",
    )

    events = await sharing.list_audit_events(store, org_id="svc-audit")
    assert len(events) == 2


# ── stats ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_stats_aggregates(store):
    await _ensure_org(store, "svc-stats")
    await _insert_lesson(store, org_id="svc-stats")
    await sharing.record_audit_event(
        store, org_id="svc-stats", event_type="share", initiated_by="k",
    )

    stats = await sharing.get_stats(store, org_id="svc-stats")
    assert stats.count_shared == 1
    assert dict(stats.audit_summary) == {"share": 1}


# ── purge ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_rejects_bad_confirmation(store):
    await _ensure_org(store, "svc-purge-bad")

    with pytest.raises(ValueError, match="PURGE"):
        await sharing.purge(
            store, org_id="svc-purge-bad", confirmation="wrong", initiated_by="k",
        )


@pytest.mark.asyncio
async def test_purge_executes_and_writes_audit(store):
    await _ensure_org(store, "svc-purge-ok")
    await _insert_lesson(store, org_id="svc-purge-ok")
    await _insert_lesson(store, org_id="svc-purge-ok")

    deleted = await sharing.purge(
        store, org_id="svc-purge-ok", confirmation="PURGE", initiated_by="key-purge",
    )
    assert deleted == 2

    # Purge audit event should land in sharing_audit (note: purge clears audit
    # FIRST then writes a fresh purge entry afterwards, so exactly one entry.)
    events = await sharing.list_audit_events(store, org_id="svc-purge-ok")
    assert len(events) == 1
    assert events[0].event_type == "purge"
    assert events[0].initiated_by == "key-purge"


# ── rate ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_lesson_validates_delta(store):
    await _ensure_org(store, "svc-rate-bad")

    with pytest.raises(ValueError, match="delta"):
        await sharing.rate_lesson(
            store,
            lesson_id="x",
            org_id="svc-rate-bad",
            delta=2,
            initiated_by="k",
        )


@pytest.mark.asyncio
async def test_rate_lesson_increments(store):
    await _ensure_org(store, "svc-rate-ok")
    lid = await _insert_lesson(store, org_id="svc-rate-ok", reputation_score=10)

    score = await sharing.rate_lesson(
        store, lesson_id=lid, org_id="svc-rate-ok", delta=1, initiated_by="k",
    )
    assert score == 11


@pytest.mark.asyncio
async def test_rate_lesson_missing_returns_none(store):
    await _ensure_org(store, "svc-rate-missing")

    score = await sharing.rate_lesson(
        store,
        lesson_id="not-real",
        org_id="svc-rate-missing",
        delta=1,
        initiated_by="k",
    )
    assert score is None
