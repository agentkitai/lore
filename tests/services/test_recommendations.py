"""Service-level tests for lore.services.recommendations using a real Postgres store."""

from __future__ import annotations

import uuid

import pytest

from lore.services.recommendations import (
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_ENABLED,
    DEFAULT_MAX_SUGGESTIONS,
    get_config,
    recommend,
    submit_feedback,
    update_config,
)

# ── constants ─────────────────────────────────────────────────────────────────

# "solo" is a pre-seeded org; other test data uses isolated UUIDs per test
_ORG = "solo"


# ── helpers ───────────────────────────────────────────────────────────────────


async def _insert_config_row(
    store,
    *,
    workspace_id=None,
    agent_id=None,
    aggressiveness=0.6,
    enabled=True,
    max_suggestions=5,
    cooldown_minutes=10,
) -> str:
    """Insert a recommendation_config row via raw SQL and return its id."""
    cfg_id = f"reccfg_{uuid.uuid4().hex[:16]}"
    conn = store._conn
    await conn.execute(
        """
        INSERT INTO recommendation_config
            (id, workspace_id, agent_id, aggressiveness, enabled, max_suggestions, cooldown_minutes)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        cfg_id,
        workspace_id,
        agent_id,
        aggressiveness,
        enabled,
        max_suggestions,
        cooldown_minutes,
    )
    return cfg_id


async def _insert_memory_with_embedding(
    store,
    *,
    org_id: str = _ORG,
    content: str = "some memory content",
) -> str:
    """Insert a memory row with a fake embedding vector and return its id."""
    mem_id = f"mem_{uuid.uuid4().hex[:16]}"
    # 384-dimensional zero vector (pgvector format)
    embedding_str = "[" + ",".join(["0.1"] * 384) + "]"
    conn = store._conn
    await conn.execute(
        """
        INSERT INTO memories
            (id, org_id, content, context, embedding, importance_score, access_count)
        VALUES ($1, $2, $3, '', $4::vector, 0.5, 0)
        """,
        mem_id,
        org_id,
        content,
        embedding_str,
    )
    return mem_id


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_config_returns_defaults_when_missing(store):
    """With no rows in the table, get_config returns all 4 default values."""
    cfg = await get_config(store)
    assert cfg["aggressiveness"] == DEFAULT_AGGRESSIVENESS
    assert cfg["enabled"] == DEFAULT_ENABLED
    assert cfg["max_suggestions"] == DEFAULT_MAX_SUGGESTIONS
    assert cfg["cooldown_minutes"] == DEFAULT_COOLDOWN_MINUTES


@pytest.mark.asyncio
async def test_get_config_returns_stored_values(store):
    """Pre-inserted row with custom values is returned by get_config."""
    await _insert_config_row(
        store,
        aggressiveness=0.75,
        enabled=False,
        max_suggestions=7,
        cooldown_minutes=20,
    )
    cfg = await get_config(store)
    assert cfg["aggressiveness"] == pytest.approx(0.75)
    assert cfg["enabled"] is False
    assert cfg["max_suggestions"] == 7
    assert cfg["cooldown_minutes"] == 20


@pytest.mark.asyncio
async def test_update_config_inserts_then_returns_dict(store):
    """update_config inserts a row and returns the stored values as a dict."""
    result = await update_config(store, aggressiveness=0.9)
    assert isinstance(result, dict)
    assert result["aggressiveness"] == pytest.approx(0.9)

    # Subsequent get_config should reflect the stored value
    cfg = await get_config(store)
    assert cfg["aggressiveness"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_update_config_preserves_none_fields(store):
    """Upserting with None for 3 fields preserves the original values."""
    # First upsert: set all 4 fields explicitly
    await update_config(
        store,
        aggressiveness=0.3,
        enabled=False,
        max_suggestions=9,
        cooldown_minutes=45,
    )

    # Second upsert: only change aggressiveness
    result = await update_config(store, aggressiveness=0.7)
    assert result["aggressiveness"] == pytest.approx(0.7)
    assert result["enabled"] is False
    assert result["max_suggestions"] == 9
    assert result["cooldown_minutes"] == 45


@pytest.mark.asyncio
async def test_submit_feedback_validates_value(store):
    """submit_feedback raises ValueError for invalid feedback strings."""
    with pytest.raises(ValueError, match="positive.*negative|negative.*positive"):
        await submit_feedback(
            store,
            org_id=_ORG,
            memory_id="mem_abc",
            actor_id="actor_1",
            feedback="bogus",
        )


@pytest.mark.asyncio
async def test_submit_feedback_records_row(store):
    """submit_feedback inserts a row that can be verified via raw SELECT."""
    mem_id = f"mem_{uuid.uuid4().hex[:8]}"
    actor_id = f"actor_{uuid.uuid4().hex[:8]}"

    await submit_feedback(
        store,
        org_id=_ORG,
        memory_id=mem_id,
        actor_id=actor_id,
        feedback="positive",
        workspace_id="ws-test",
    )

    conn = store._conn
    row = await conn.fetchrow(
        "SELECT org_id, memory_id, actor_id, feedback FROM recommendation_feedback "
        "WHERE memory_id = $1",
        mem_id,
    )
    assert row is not None
    assert row["org_id"] == _ORG
    assert row["actor_id"] == actor_id
    assert row["feedback"] == "positive"


@pytest.mark.asyncio
async def test_recommend_returns_empty_when_context_blank(store):
    """recommend() with empty context string returns []."""
    result = await recommend(store, org_id=_ORG, context="")
    assert result == []


@pytest.mark.asyncio
async def test_recommend_returns_empty_when_no_candidates(store):
    """recommend() returns [] when no memories with embeddings exist for the org."""
    result = await recommend(
        store,
        org_id=f"no-memories-{uuid.uuid4().hex[:8]}",
        context="some context",
    )
    assert result == []


@pytest.mark.asyncio
async def test_recommend_returns_engine_results(store, monkeypatch):
    """recommend() calls the engine and returns its results."""
    await _insert_memory_with_embedding(store, org_id=_ORG)

    sentinel = object()

    class FakeEmbedder:
        def embed(self, text: str):
            return [0.1] * 384

    class FakeEngine:
        def __init__(self, *_, **__):
            pass

        def suggest(self, *, context, session_entities, limit):
            return [sentinel]

    monkeypatch.setattr("lore.recommend.engine.RecommendationEngine", FakeEngine)
    monkeypatch.setattr("lore.embed.LocalEmbedder", FakeEmbedder)

    result = await recommend(store, org_id=_ORG, context="relevant context")
    assert result == [sentinel]


@pytest.mark.asyncio
async def test_recommend_swallows_engine_error(store, monkeypatch):
    """recommend() returns [] when the engine raises an exception."""
    await _insert_memory_with_embedding(store, org_id=_ORG)

    class BrokenEngine:
        def __init__(self, *_, **__):
            raise RuntimeError("engine failed")

    class FakeEmbedder:
        def embed(self, text: str):
            return [0.1] * 384

    monkeypatch.setattr("lore.recommend.engine.RecommendationEngine", BrokenEngine)
    monkeypatch.setattr("lore.embed.LocalEmbedder", FakeEmbedder)

    result = await recommend(store, org_id=_ORG, context="some context")
    assert result == []
