"""Smoke test: the parametrized store fixture provides a Store for each backend."""

from __future__ import annotations

import pytest

from lore.persistence import Store


@pytest.mark.asyncio
async def test_store_fixture_provides_store(store: Store):
    assert hasattr(store, "insert_memory")
    assert hasattr(store, "recall_by_embedding")
