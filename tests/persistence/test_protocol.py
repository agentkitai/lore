"""Tests that the Store Protocol declares the MemoryOps and GraphOps slices."""

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

REQUIRED_GRAPH_OPS = {
    "get_entity",
    "get_entity_by_name",
    "list_entities",
    "upsert_entity",
    "update_entity_counts",
    "delete_entity",
    "get_mentions_for_memory",
    "get_mentions_for_entity",
    "save_mention",
    "count_memories_for_entity",
    "get_relationship",
    "get_active_relationship",
    "list_relationships_for_entity",
    "save_relationship",
    "update_relationship_status",
    "update_relationship_weight",
    "expire_relationship",
    "list_pending_relationships",
    "save_rejected_pattern",
    "query_relationships",
    "get_graph_stats",
    "get_timeline_buckets",
    "get_memories_by_entities",
    "search_memories_text",
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


def test_store_declares_graph_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_GRAPH_OPS - members
    assert not missing, f"Store missing GraphOps methods: {missing}"


def test_graph_ops_are_async():
    for name in REQUIRED_GRAPH_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )
