"""Shared fixtures for integration tests."""

from __future__ import annotations

from typing import List

import pytest

from lore import Lore
from lore.store.memory import MemoryStore


def _stub_embed(text: str) -> List[float]:
    """Deterministic embedding that varies by input text.

    Uses hash-based approach so different texts get different vectors,
    enabling meaningful cosine similarity comparisons.
    """
    h = hash(text)
    vec: List[float] = []
    for i in range(384):
        # Mix hash with index to produce varied dimensions
        val = ((h + i * 997) % 10000) / 10000.0
        vec.append(val)
    return vec


@pytest.fixture
def memory_store() -> MemoryStore:
    """Fresh in-memory store for each test."""
    return MemoryStore()


@pytest.fixture
def stub_embed():
    """Return the deterministic stub embedding function."""
    return _stub_embed


@pytest.fixture
def lore_no_llm(memory_store: MemoryStore) -> Lore:
    """Lore instance without any LLM provider configured."""
    return Lore(store=memory_store, embedding_fn=_stub_embed, redact=False)


@pytest.fixture
def lore_with_graph(memory_store: MemoryStore) -> Lore:
    """Lore instance with knowledge_graph=True but no LLM.

    Only co-occurrence edges can be created (from enrichment entities),
    but graph infrastructure (traverser, entity manager) is initialized.
    """
    return Lore(
        store=memory_store,
        embedding_fn=_stub_embed,
        redact=False,
        knowledge_graph=True,
    )
