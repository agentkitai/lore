"""Contract tests for the RecommendationOps slice of Store.

Covers get_recommendation_config and upsert_recommendation_config.
record_recommendation_feedback and list_candidate_memories_for_recommendation
are tested in T4/T5.
"""

from __future__ import annotations

import pytest

from lore.persistence import Store
from lore.persistence.types import StoredRecommendationConfig

# ── get_recommendation_config ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_config_returns_none_when_missing(store: Store):
    result = await store.get_recommendation_config()
    assert result is None


@pytest.mark.asyncio
async def test_upsert_config_inserts_when_missing(store: Store):
    result = await store.upsert_recommendation_config(
        aggressiveness=0.7,
        enabled=True,
        max_suggestions=5,
        cooldown_minutes=20,
    )
    assert isinstance(result, StoredRecommendationConfig)
    assert result.id.startswith("reccfg_")
    assert result.workspace_id is None
    assert result.agent_id is None
    assert result.aggressiveness == pytest.approx(0.7)
    assert result.enabled is True
    assert result.max_suggestions == 5
    assert result.cooldown_minutes == 20
    assert result.updated_at is not None

    # subsequent get returns same row
    fetched = await store.get_recommendation_config()
    assert fetched is not None
    assert fetched.id == result.id
    assert fetched.aggressiveness == pytest.approx(0.7)
    assert fetched.max_suggestions == 5


@pytest.mark.asyncio
async def test_upsert_config_updates_existing(store: Store):
    first = await store.upsert_recommendation_config(
        aggressiveness=0.5,
        enabled=True,
        max_suggestions=3,
        cooldown_minutes=15,
    )

    second = await store.upsert_recommendation_config(
        aggressiveness=0.8,
        enabled=True,
        max_suggestions=3,
        cooldown_minutes=15,
    )

    assert second.id == first.id
    assert second.aggressiveness == pytest.approx(0.8)
    assert second.updated_at >= first.updated_at


@pytest.mark.asyncio
async def test_upsert_config_preserves_none_fields(store: Store):
    # Initial upsert with all four fields
    await store.upsert_recommendation_config(
        aggressiveness=0.6,
        enabled=False,
        max_suggestions=7,
        cooldown_minutes=30,
    )

    # Second upsert with only aggressiveness changed; rest None
    await store.upsert_recommendation_config(aggressiveness=0.9)

    fetched = await store.get_recommendation_config()
    assert fetched is not None
    assert fetched.aggressiveness == pytest.approx(0.9)
    # These must be unchanged at their original values
    assert fetched.enabled is False
    assert fetched.max_suggestions == 7
    assert fetched.cooldown_minutes == 30


@pytest.mark.asyncio
async def test_upsert_config_with_workspace_id_creates_separate_row(store: Store):
    global_cfg = await store.upsert_recommendation_config(
        aggressiveness=0.5,
    )
    ws_cfg = await store.upsert_recommendation_config(
        workspace_id="ws_abc",
        aggressiveness=0.9,
    )

    assert global_cfg.id != ws_cfg.id
    assert global_cfg.workspace_id is None
    assert ws_cfg.workspace_id == "ws_abc"


@pytest.mark.asyncio
async def test_get_config_distinguishes_workspace_scopes(store: Store):
    await store.upsert_recommendation_config(aggressiveness=0.3)
    await store.upsert_recommendation_config(workspace_id="ws_xyz", aggressiveness=0.8)

    global_cfg = await store.get_recommendation_config()
    ws_cfg = await store.get_recommendation_config(workspace_id="ws_xyz")

    assert global_cfg is not None
    assert ws_cfg is not None
    assert global_cfg.aggressiveness == pytest.approx(0.3)
    assert ws_cfg.aggressiveness == pytest.approx(0.8)
    assert global_cfg.id != ws_cfg.id


@pytest.mark.asyncio
async def test_upsert_first_call_uses_defaults_when_all_none(store: Store):
    result = await store.upsert_recommendation_config()

    assert result.aggressiveness == pytest.approx(0.5)
    assert result.enabled is True
    assert result.max_suggestions == 3
    assert result.cooldown_minutes == 15
