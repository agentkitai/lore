"""Service tests for lore.services.topics_dashboard."""

from __future__ import annotations

import pytest

from lore.persistence import NewEntity, NewMemory, NewMention
from lore.services import topics_dashboard

# ── helpers ───────────────────────────────────────────────────────────────────


def _vec(seed: int = 0) -> list[float]:
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


async def _ensure_org(store, org_id: str) -> None:
    await store._conn.execute(
        "INSERT INTO orgs (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        org_id,
        org_id,
    )


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_topics_returns_dict_shape(store):
    """list_topics returns a dict with topics/total/threshold keys."""
    # Insert an entity with enough mentions
    entity = await store.upsert_entity(
        NewEntity(name="td-hot-topic", entity_type="concept", mention_count=10)
    )

    result = await topics_dashboard.list_topics(store, min_mentions=5, limit=50)

    assert "topics" in result
    assert "total" in result
    assert "threshold" in result
    assert result["threshold"] == 5
    names = {t["name"] for t in result["topics"]}
    assert entity.name in names

    # Verify topic shape
    topic = next(t for t in result["topics"] if t["name"] == entity.name)
    assert "entity_id" in topic
    assert "entity_type" in topic
    assert "mention_count" in topic
    assert "first_seen_at" in topic
    assert "last_seen_at" in topic
    assert "related_entity_count" in topic


@pytest.mark.asyncio
async def test_get_topic_detail_returns_none_for_missing(store):
    """get_topic_detail returns None when entity does not exist."""
    result = await topics_dashboard.get_topic_detail(
        store, name="nonexistent-entity-xyz"
    )
    assert result is None


@pytest.mark.asyncio
async def test_get_topic_detail_brief_truncates_content(store):
    """format='brief' truncates memory content to 100 chars with ellipsis."""
    org = "td-brief-org"
    await _ensure_org(store, org)

    entity = await store.upsert_entity(
        NewEntity(name="td-brief-entity", entity_type="topic", mention_count=5)
    )

    # Insert a memory with content longer than 100 chars
    long_content = "x" * 200
    mem = await store.insert_memory(
        NewMemory(org_id=org, content=long_content, embedding=_vec(1))
    )

    # Link memory to entity via mention
    await store.save_mention(NewMention(entity_id=entity.id, memory_id=mem.id))

    result = await topics_dashboard.get_topic_detail(
        store, name="td-brief-entity", format="brief"
    )

    assert result is not None
    assert "memories" in result
    assert "entity" in result
    assert "memory_count" in result

    # Check that the long content was truncated
    if result["memories"]:
        m = result["memories"][0]
        assert len(m["content"]) <= 103  # 100 chars + "..."
        assert m["content"].endswith("...")
