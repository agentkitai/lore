"""Tests that the Store Protocol declares the MemoryOps slice."""

from __future__ import annotations

import inspect

from lore.persistence import Store

REQUIRED_MEMORY_OPS = {
    "insert_memory",
    "get_memory",
    "update_memory",
    "delete_memory",
    "list_memories",
    "recall_by_embedding",
    "expire_memories",
    "bump_access_counts",
    "vote_memory",
}


def test_store_declares_memory_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_MEMORY_OPS - members
    assert not missing, f"Store missing MemoryOps methods: {missing}"


def test_memory_ops_are_async():
    for name in REQUIRED_MEMORY_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )
