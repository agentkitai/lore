"""Contract tests for the SharingOps slice of Store — config, agent, deny, audit, stats, purge, rate.

These tests run against every Store implementation (Phase 1L: Postgres only).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pytest

from lore.persistence import (
    AgentSharingConfigData,
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
