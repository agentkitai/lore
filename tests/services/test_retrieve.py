# tests/services/test_retrieve.py
"""Tests for the retrieve service (without analytics — that's left at the route)."""

from __future__ import annotations

import pytest

from lore.services.memories import create_memory
from lore.services.retrieve import retrieve, RetrieveOutput


@pytest.mark.asyncio
async def test_retrieve_returns_ranked_memories(store):
    # Insert one memory; query with the same embedding
    embed = [0.1] * 384
    await create_memory(
        store, org_id="solo", content="alpha doc", embedding=embed
    )
    out: RetrieveOutput = await retrieve(
        store,
        org_id="solo",
        query_text="alpha",
        query_vec=embed,
        limit=5,
        min_score=0.0,
    )
    assert out.count >= 1
    assert any(m.content == "alpha doc" for m in out.memories)
    assert isinstance(out.formatted, str)


@pytest.mark.asyncio
async def test_retrieve_format_xml(store):
    embed = [0.2] * 384
    await create_memory(
        store, org_id="solo", content="xml me", embedding=embed
    )
    out = await retrieve(
        store, org_id="solo", query_text="xml", query_vec=embed,
        limit=5, min_score=0.0, format="xml",
    )
    assert "<memories" in out.formatted


@pytest.mark.asyncio
async def test_retrieve_invalid_format_raises():
    with pytest.raises(ValueError):
        await retrieve(
            store=None,  # type: ignore[arg-type]
            org_id="solo",
            query_text="x",
            query_vec=[0.0] * 384,
            limit=5,
            min_score=0.0,
            format="bogus",
        )
