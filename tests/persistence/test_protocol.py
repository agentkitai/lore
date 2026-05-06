"""Tests that the Store Protocol declares the MemoryOps, GraphOps, PolicyOps, WorkspaceOps, AuthOps, AnalyticsOps, RecommendationOps, and ConversationOps slices."""

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
    "enrich_memory_meta",
    "import_extracted_memory",
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


REQUIRED_POLICY_OPS = {
    "get_profile",
    "get_profile_by_name",
    "list_profiles",
    "create_profile",
    "update_profile",
    "delete_profile",
    "resolve_profile_for_key",
}


def test_store_declares_policy_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_POLICY_OPS - members
    assert not missing, f"Store missing PolicyOps methods: {missing}"


def test_policy_ops_are_async():
    for name in REQUIRED_POLICY_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )


REQUIRED_WORKSPACE_OPS = {
    "get_workspace",
    "list_workspaces",
    "create_workspace",
    "update_workspace",
    "archive_workspace",
    "add_workspace_member",
    "list_workspace_members",
    "update_workspace_member_role",
    "remove_workspace_member",
}


def test_store_declares_workspace_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_WORKSPACE_OPS - members
    assert not missing, f"Store missing WorkspaceOps methods: {missing}"


def test_workspace_ops_are_async():
    for name in REQUIRED_WORKSPACE_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )


REQUIRED_AUTH_OPS = {
    "get_api_key",
    "list_api_keys",
    "create_api_key",
    "revoke_api_key",
    "count_active_root_keys",
}


def test_store_declares_auth_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_AUTH_OPS - members
    assert not missing, f"Store missing AuthOps methods: {missing}"


def test_auth_ops_are_async():
    for name in REQUIRED_AUTH_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )


REQUIRED_ANALYTICS_OPS = {
    "record_retrieval_event",
    "bump_access_counts",
    "record_memory_access",
    "list_recent_session_snapshots",
}


def test_store_declares_analytics_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_ANALYTICS_OPS - members
    assert not missing, f"Store missing AnalyticsOps methods: {missing}"


def test_analytics_ops_are_async():
    for name in REQUIRED_ANALYTICS_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )


REQUIRED_RECOMMENDATION_OPS = {
    "get_recommendation_config",
    "upsert_recommendation_config",
    "record_recommendation_feedback",
    "list_candidate_memories_for_recommendation",
}


def test_store_declares_recommendation_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_RECOMMENDATION_OPS - members
    assert not missing, f"Store missing RecommendationOps methods: {missing}"


def test_recommendation_ops_are_async():
    for name in REQUIRED_RECOMMENDATION_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )


REQUIRED_CONVERSATION_OPS = {
    "create_conversation_job",
    "get_conversation_job",
    "mark_conversation_job_processing",
    "complete_conversation_job",
    "fail_conversation_job",
}


def test_store_declares_conversation_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_CONVERSATION_OPS - members
    assert not missing, f"Store missing ConversationOps methods: {missing}"


def test_conversation_ops_are_async():
    for name in REQUIRED_CONVERSATION_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )
