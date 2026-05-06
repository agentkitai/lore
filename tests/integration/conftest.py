"""Shared fixtures for integration tests."""

from __future__ import annotations

import hashlib
from typing import List

import pytest

from lore import Lore
from lore.store.memory import MemoryStore


def _stub_embed(text: str) -> List[float]:
    """Deterministic embedding that varies by input text.

    Uses SHA-256 (process-independent) instead of the built-in `hash()`,
    which is randomized via PYTHONHASHSEED and was causing
    test_consolidate_no_duplicates to flake across Python versions in CI
    when two unrelated strings happened to produce a near-identical
    seed-dependent vector.
    """
    h = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
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
