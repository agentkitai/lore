"""Service-level tests using a real Postgres store."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from lore.persistence.exceptions import StoreNotFoundError
from lore.services.memories import (
    create_memory,
    delete_memory,
    enrich_memory_async,
    get_memory,
    list_memories,
    record_memory_access,
    update_memory,
    vote_memory,
)


@pytest.mark.asyncio
async def test_create_then_get(store):
    created = await create_memory(
        store,
        org_id="solo",
        content="hello world",
        embedding=[0.0] * 384,
        tags=["a", "b"],
        project="proj",
    )
    fetched = await get_memory(store, "solo", created.id)
    assert fetched is not None
    assert fetched.content == "hello world"
    assert tuple(fetched.tags) == ("a", "b")


@pytest.mark.asyncio
async def test_update_then_get(store):
    created = await create_memory(
        store, org_id="solo", content="orig", embedding=[0.0] * 384
    )
    updated = await update_memory(
        store, org_id="solo", memory_id=created.id, content="updated"
    )
    assert updated.content == "updated"


@pytest.mark.asyncio
async def test_list_filters(store):
    await create_memory(store, org_id="solo", content="a", embedding=[0.0] * 384, project="x")
    await create_memory(store, org_id="solo", content="b", embedding=[0.0] * 384, project="y")
    only_x = await list_memories(store, org_id="solo", project="x")
    assert {m.content for m in only_x} == {"a"}


@pytest.mark.asyncio
async def test_delete(store):
    created = await create_memory(
        store, org_id="solo", content="bye", embedding=[0.0] * 384
    )
    deleted = await delete_memory(store, org_id="solo", memory_id=created.id)
    assert deleted is True
    assert (await get_memory(store, "solo", created.id)) is None


@pytest.mark.asyncio
async def test_vote(store):
    created = await create_memory(
        store, org_id="solo", content="rate me", embedding=[0.0] * 384
    )
    after = await vote_memory(store, org_id="solo", memory_id=created.id, direction="up")
    assert after.upvotes == 1


# ── Enrichment + access tests ──────────────────────────────────────


class _FakePipeline:
    def __init__(self, *_, **__):
        pass

    def enrich(self, content, context=None):
        return {"summary": "x"}


@pytest.mark.asyncio
async def test_enrich_memory_async_calls_pipeline_and_persists(store, monkeypatch):
    # ``enrich_memory_async`` calls ``store.enrich_memory_meta`` and the
    # service swallows NotImplementedError to a warning — so the contract
    # hook can't see the original. Skip cleanly on SqliteStore until 3D+.
    from tests.persistence.conftest import _is_sqlite
    if _is_sqlite(store):
        pytest.skip("SqliteStore.enrich_memory_meta pending Phase 3D+")

    monkeypatch.setattr("lore.enrichment.pipeline.EnrichmentPipeline", _FakePipeline)
    monkeypatch.setattr("lore.enrichment.llm.LLMClient", lambda **_: object())

    created = await create_memory(
        store, org_id="solo", content="enrich me", embedding=[0.0] * 384
    )
    await enrich_memory_async(
        store, memory_id=created.id, content="enrich me", context=None
    )

    fetched = await get_memory(store, "solo", created.id)
    assert fetched is not None
    assert fetched.meta.get("enrichment") == {"summary": "x"}


@pytest.mark.asyncio
async def test_enrich_memory_async_skips_persist_when_pipeline_returns_none(
    store, monkeypatch
):
    class _NullPipeline:
        def __init__(self, *_, **__):
            pass

        def enrich(self, content, context=None):
            return None

    monkeypatch.setattr("lore.enrichment.pipeline.EnrichmentPipeline", _NullPipeline)
    monkeypatch.setattr("lore.enrichment.llm.LLMClient", lambda **_: object())

    mock_enrich = AsyncMock()
    monkeypatch.setattr(store, "enrich_memory_meta", mock_enrich)

    await enrich_memory_async(
        store, memory_id="fake-id", content="test", context=None
    )

    mock_enrich.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_memory_async_swallows_pipeline_error(store, monkeypatch):
    class _ErrorPipeline:
        def __init__(self, *_, **__):
            pass

        def enrich(self, content, context=None):
            raise RuntimeError("boom")

    monkeypatch.setattr("lore.enrichment.pipeline.EnrichmentPipeline", _ErrorPipeline)
    monkeypatch.setattr("lore.enrichment.llm.LLMClient", lambda **_: object())

    # Should not raise
    result = await enrich_memory_async(
        store, memory_id="fake-id", content="test", context=None
    )
    assert result is None


@pytest.mark.asyncio
async def test_record_memory_access_returns_updated_row(store):
    created = await create_memory(
        store, org_id="solo", content="access me", embedding=[0.0] * 384
    )
    updated = await record_memory_access(store, "solo", created.id)
    assert updated.access_count == 1


@pytest.mark.asyncio
async def test_record_memory_access_raises_not_found(store):
    with pytest.raises(StoreNotFoundError):
        await record_memory_access(store, "solo", "00000000-0000-0000-0000-000000000000")
