"""Contract tests for the RecommendationOps slice of Store.

Covers get_recommendation_config, upsert_recommendation_config,
record_recommendation_feedback, and list_candidate_memories_for_recommendation
(T5).
"""

from __future__ import annotations

import json

import pytest

from lore.persistence import Store
from lore.persistence.types import NewRecommendationFeedback, RecommendationCandidate, StoredRecommendationConfig

# ── helpers ───────────────────────────────────────────────────────────────────

_UNSET = object()


async def _insert_memory_with_embedding(
    store,
    *,
    memory_id=None,
    org_id="solo",
    content="x",
    embedding=None,
    importance_score=_UNSET,
    meta=None,
) -> str:
    from ulid import ULID

    mid = memory_id or f"mem_{ULID()}"
    embedding_param = json.dumps(list(embedding)) if embedding is not None else None
    meta_param = json.dumps(dict(meta or {}))
    # importance_score=_UNSET means use default 0.5; explicit None inserts NULL
    importance = None if importance_score is None else (0.5 if importance_score is _UNSET else importance_score)
    await store._conn.execute(
        """INSERT INTO memories (id, org_id, content, context, tags, confidence, embedding, meta, importance_score)
           VALUES ($1, $2, $3, '', '[]'::jsonb, 0.5, $4::vector, $5::jsonb, $6)""",
        mid,
        org_id,
        content,
        embedding_param,
        meta_param,
        importance,
    )
    return mid

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


# ── record_recommendation_feedback ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_feedback_inserts_row(store: Store):
    fb = NewRecommendationFeedback(
        org_id="org_test",
        memory_id="mem_abc",
        actor_id="actor_1",
        feedback="positive",
    )
    await store.record_recommendation_feedback(fb)

    count = await store._conn.fetchval(
        "SELECT COUNT(*) FROM recommendation_feedback WHERE memory_id = $1",
        "mem_abc",
    )
    assert count == 1


@pytest.mark.asyncio
async def test_record_feedback_with_workspace_id(store: Store):
    fb = NewRecommendationFeedback(
        org_id="org_test",
        memory_id="mem_ws",
        actor_id="actor_2",
        feedback="negative",
        workspace_id="ws_xyz",
    )
    await store.record_recommendation_feedback(fb)

    row = await store._conn.fetchrow(
        "SELECT id, signal, context_hash, workspace_id FROM recommendation_feedback WHERE memory_id = $1",
        "mem_ws",
    )
    assert row is not None
    assert row["workspace_id"] == "ws_xyz"


@pytest.mark.asyncio
async def test_record_feedback_with_signal_and_context_hash(store: Store):
    fb = NewRecommendationFeedback(
        org_id="org_test",
        memory_id="mem_sig",
        actor_id="actor_3",
        feedback="positive",
        signal="auto",
        context_hash="abc123hash",
    )
    await store.record_recommendation_feedback(fb)

    row = await store._conn.fetchrow(
        "SELECT id, signal, context_hash, workspace_id FROM recommendation_feedback WHERE memory_id = $1",
        "mem_sig",
    )
    assert row is not None
    assert row["signal"] == "auto"
    assert row["context_hash"] == "abc123hash"


@pytest.mark.asyncio
async def test_record_feedback_generates_recfb_prefix_id(store: Store):
    fb = NewRecommendationFeedback(
        org_id="org_test",
        memory_id="mem_id_check",
        actor_id="actor_4",
        feedback="positive",
    )
    await store.record_recommendation_feedback(fb)

    row = await store._conn.fetchrow(
        "SELECT id, signal, context_hash, workspace_id FROM recommendation_feedback WHERE memory_id = $1",
        "mem_id_check",
    )
    assert row is not None
    assert row["id"].startswith("recfb_")


# ── list_candidate_memories_for_recommendation (T5) ───────────────────────────

_EMB = [0.1] * 384


@pytest.mark.asyncio
async def test_list_candidates_returns_memories_with_embeddings(store: Store):
    await _insert_memory_with_embedding(store, org_id="solo", content="alpha", embedding=_EMB)
    await _insert_memory_with_embedding(store, org_id="solo", content="beta", embedding=_EMB)

    results = await store.list_candidate_memories_for_recommendation("solo")
    assert len(results) == 2
    assert all(isinstance(r, RecommendationCandidate) for r in results)


@pytest.mark.asyncio
async def test_list_candidates_excludes_null_embedding(store: Store):
    # Insert one memory with embedding via helper
    mid_with = await _insert_memory_with_embedding(
        store, org_id="solo", content="has-emb", embedding=_EMB
    )
    # Insert one memory with NULL embedding via raw SQL
    from ulid import ULID
    mid_null = f"mem_{ULID()}"
    await store._conn.execute(
        """INSERT INTO memories (id, org_id, content, context, tags, confidence, embedding, importance_score)
           VALUES ($1, $2, $3, '', '[]'::jsonb, 0.5, NULL, 0.5)""",
        mid_null,
        "solo",
        "no-emb",
    )

    results = await store.list_candidate_memories_for_recommendation("solo")
    ids = [r.id for r in results]
    assert mid_with in ids
    assert mid_null not in ids
    assert len(results) == 1


@pytest.mark.asyncio
async def test_list_candidates_org_isolation(store: Store):
    mid_a = await _insert_memory_with_embedding(store, org_id="org_a", content="in-a", embedding=_EMB)
    mid_b = await _insert_memory_with_embedding(store, org_id="org_b", content="in-b", embedding=_EMB)

    results_a = await store.list_candidate_memories_for_recommendation("org_a")
    ids_a = [r.id for r in results_a]
    assert mid_a in ids_a
    assert mid_b not in ids_a
    assert len(results_a) == 1


@pytest.mark.asyncio
async def test_list_candidates_ordered_by_importance_score_desc_nulls_last(store: Store):
    mid_null = await _insert_memory_with_embedding(
        store, org_id="solo", content="null-imp", embedding=_EMB, importance_score=None
    )
    mid_low = await _insert_memory_with_embedding(
        store, org_id="solo", content="low-imp", embedding=_EMB, importance_score=0.5
    )
    mid_high = await _insert_memory_with_embedding(
        store, org_id="solo", content="high-imp", embedding=_EMB, importance_score=0.9
    )

    results = await store.list_candidate_memories_for_recommendation("solo")
    ids = [r.id for r in results]
    assert ids.index(mid_high) < ids.index(mid_low)
    assert ids.index(mid_low) < ids.index(mid_null)


@pytest.mark.asyncio
async def test_list_candidates_respects_limit(store: Store):
    for i in range(5):
        await _insert_memory_with_embedding(
            store, org_id="solo", content=f"mem-{i}", embedding=_EMB
        )

    results = await store.list_candidate_memories_for_recommendation("solo", limit=2)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_list_candidates_decodes_meta_json(store: Store):
    await _insert_memory_with_embedding(
        store, org_id="solo", content="meta-test", embedding=_EMB, meta={"foo": "bar"}
    )

    results = await store.list_candidate_memories_for_recommendation("solo")
    assert len(results) == 1
    assert results[0].metadata == {"foo": "bar"}
