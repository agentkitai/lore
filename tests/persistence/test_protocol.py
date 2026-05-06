"""Tests that the Store Protocol declares all required operation slices.

Covers: MemoryOps, GraphOps, PolicyOps, WorkspaceOps, AuthOps, AnalyticsOps,
RecommendationOps, ConversationOps, AuditOps, and RetentionOps.
"""

from __future__ import annotations

import inspect

from lore.persistence import Store

REQUIRED_MEMORY_OPS = {
    "insert_memory",
    "get_memory",
    "update_memory",
    "delete_memory",
    "list_memories",
    "list_memories_paginated",
    "list_memories_with_embeddings",
    "recall_by_embedding",
    "expire_memories",
    "bump_access_counts",
    "vote_memory",
    "enrich_memory_meta",
    "import_extracted_memory",
    "upsert_memory_with_embedding",
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
    "compute_retrieval_analytics",
    "compute_metric_value",
    "compute_metric_timeseries",
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


REQUIRED_AUDIT_OPS = {"query_audit_log"}


def test_store_declares_audit_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_AUDIT_OPS - members
    assert not missing, f"Store missing AuditOps methods: {missing}"


def test_audit_ops_are_async():
    for name in REQUIRED_AUDIT_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )


REQUIRED_RETENTION_OPS = {
    "list_retention_policies",
    "get_retention_policy",
    "create_retention_policy",
    "update_retention_policy",
    "delete_retention_policy",
    "get_latest_snapshot_for_policy",
    "count_snapshots_for_policy",
    "record_drill_result",
    "list_drill_results_for_policy",
    "get_latest_drill_result",
}


def test_store_declares_retention_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_RETENTION_OPS - members
    assert not missing, f"Store missing RetentionOps methods: {missing}"


def test_retention_ops_are_async():
    for name in REQUIRED_RETENTION_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )


REQUIRED_SLO_OPS = {
    "list_slo_definitions",
    "get_slo_definition",
    "create_slo_definition",
    "update_slo_definition",
    "delete_slo_definition",
    "list_slo_alerts",
    "record_slo_alert",
}


def test_store_declares_slo_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_SLO_OPS - members
    assert not missing, f"Store missing SloOps methods: {missing}"


def test_slo_ops_are_async():
    for name in REQUIRED_SLO_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )


REQUIRED_SHARING_OPS = {
    "get_or_init_sharing_config",
    "update_sharing_config",
    "list_agent_sharing_configs",
    "upsert_agent_sharing_config",
    "list_deny_rules",
    "create_deny_rule",
    "delete_deny_rule",
    "list_audit_events",
    "record_audit_event",
    "get_sharing_stats",
    "purge_sharing",
    "rate_lesson",
}


def test_store_declares_sharing_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_SHARING_OPS - members
    assert not missing, f"Store missing SharingOps methods: {missing}"


def test_sharing_ops_are_async():
    for name in REQUIRED_SHARING_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )
