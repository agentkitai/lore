"""Server-side Store Protocol.

The Store is the only place in the codebase that touches raw SQL or DB drivers.
Routes and services call typed methods declared here. Phase 1A defines the
MemoryOps slice; later phases extend the protocol with GraphOps, WorkspaceOps,
SnapshotOps, AnalyticsOps, PolicyOps, AuthOps, etc.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from lore.persistence.types import (
    ExportedMemory,
    GraphStats,
    MemoryFilter,
    MemoryPatch,
    NewApiKey,
    NewConversationJob,
    NewEntity,
    NewMember,
    NewMemory,
    NewMention,
    NewProfile,
    NewRecommendationFeedback,
    NewRelationship,
    NewRetrievalEvent,
    NewWorkspace,
    PendingRelationshipRow,
    ProfilePatch,
    RecallParams,
    RecommendationCandidate,
    RetrievalAnalyticsResult,
    ScoredMemory,
    StoredApiKey,
    StoredAuditEntry,
    StoredConversationJob,
    StoredEntity,
    StoredMember,
    StoredMemory,
    StoredMention,
    StoredProfile,
    StoredRecommendationConfig,
    StoredRelationship,
    StoredWorkspace,
    TimelineBucketRow,
    WorkspacePatch,
)


@runtime_checkable
class Store(Protocol):
    """The Store protocol.

    Implementations: PostgresStore (Phase 1A), SqliteStore (Phase 3).
    Method groups are added incrementally; Phase 1A defines MemoryOps, Phase 1B adds GraphOps.
    """

    # ── lifecycle ────────────────────────────────────────────────────

    async def close(self) -> None:
        """Release any underlying resources (pool, connection, file)."""
        ...

    # ── MemoryOps ────────────────────────────────────────────────────

    async def insert_memory(self, memory: NewMemory) -> StoredMemory:
        """Insert a memory; returns the stored row with server-generated id/timestamps."""
        ...

    async def get_memory(self, org_id: str, memory_id: str) -> Optional[StoredMemory]:
        """Return a memory by id within an org, or None if absent or expired."""
        ...

    async def update_memory(
        self, org_id: str, memory_id: str, patch: MemoryPatch
    ) -> StoredMemory:
        """Apply a patch and return the updated row. Raises StoreNotFoundError if missing."""
        ...

    async def delete_memory(self, org_id: str, memory_id: str) -> bool:
        """Delete a memory; returns True if a row was deleted."""
        ...

    async def list_memories(self, filter: MemoryFilter) -> Sequence[StoredMemory]:
        """List memories matching filter; ordered by created_at DESC."""
        ...

    async def recall_by_embedding(self, params: RecallParams) -> Sequence[ScoredMemory]:
        """Vector recall: returns memories ranked by combined score (similarity * importance * decay)."""
        ...

    async def expire_memories(self) -> int:
        """Delete rows with expires_at < now(); returns rowcount."""
        ...

    async def bump_access_counts(self, org_id: str, memory_ids: Sequence[str]) -> None:
        """Increment access_count + last_accessed_at + recompute importance_score."""
        ...

    async def vote_memory(
        self, org_id: str, memory_id: str, *, direction: str
    ) -> StoredMemory:
        """direction is 'up' or 'down'. Returns the updated memory."""
        ...

    async def enrich_memory_meta(self, memory_id: str, enrichment_data: Mapping[str, Any]) -> None:
        """Merge enrichment_data into the memory's meta JSONB column."""
        ...

    async def import_extracted_memory(
        self,
        *,
        memory_id: str,
        org_id: str,
        content: str,
        context: str,
        tags: Sequence[str],
        source: str,
        meta: Mapping[str, Any],
        confidence: float,
    ) -> bool:
        """Insert a pre-extracted memory with a caller-supplied id; returns True if inserted, False if duplicate."""
        ...

    async def list_memories_paginated(
        self, filter: MemoryFilter, *, limit: int = 50, offset: int = 0,
    ) -> tuple[int, Sequence[StoredMemory]]:
        """List memories matching filter with pagination; returns (total_count, page_of_rows)."""
        ...

    async def list_memories_with_embeddings(
        self, filter: MemoryFilter,
    ) -> Sequence[ExportedMemory]:
        """List memories with their raw embeddings included; used for export and migration."""
        ...

    async def upsert_memory_with_embedding(
        self,
        *,
        memory_id: str,
        org_id: str,
        content: str,
        context: Optional[str],
        tags: Sequence[str],
        confidence: float,
        source: Optional[str],
        project: Optional[str],
        embedding: Optional[Sequence[float]],
        expires_at: Optional[datetime],
        upvotes: int,
        downvotes: int,
        meta: Mapping[str, Any],
    ) -> bool:
        """Insert or update a memory row including its embedding vector; returns True if inserted, False if updated."""
        ...

    # ── GraphOps ─────────────────────────────────────────────────────

    # Entity ops
    async def get_entity(self, entity_id: str) -> Optional[StoredEntity]:
        """Return an entity by id, or None if absent."""
        ...

    async def get_entity_by_name(self, name: str) -> Optional[StoredEntity]:
        """Return an entity whose name matches exactly (case-sensitive); services normalize."""
        ...

    async def list_entities(
        self,
        *,
        entity_type: Optional[str] = None,
        min_mentions: int = 0,
        limit: int = 100,
    ) -> Sequence[StoredEntity]:
        """List entities filtered by type and minimum mention_count, ordered by mention_count DESC."""
        ...

    async def upsert_entity(self, entity: NewEntity) -> StoredEntity:
        """Insert or merge an entity by name; returns the stored row with id."""
        ...

    async def update_entity_counts(
        self,
        entity_id: str,
        *,
        mention_delta: int,
        last_seen_at: datetime,
    ) -> None:
        """Atomically adjust mention_count and bump last_seen_at."""
        ...

    async def delete_entity(self, entity_id: str) -> bool:
        """Delete an entity (cascades to mentions and relationships); True if removed."""
        ...

    # Mention ops
    async def get_mentions_for_memory(self, memory_id: str) -> Sequence[StoredMention]:
        """All mentions linking entities to a given memory."""
        ...

    async def get_mentions_for_entity(
        self,
        entity_id: str,
        *,
        limit: int = 100,
    ) -> Sequence[StoredMention]:
        """All mentions linking memories to a given entity, newest first."""
        ...

    async def save_mention(self, mention: NewMention) -> None:
        """Idempotent insert; (entity_id, memory_id) is unique."""
        ...

    async def count_memories_for_entity(self, entity_id: str) -> int:
        """Distinct memory count for an entity (COUNT DISTINCT memory_id)."""
        ...

    # Relationship ops
    async def get_relationship(self, rel_id: str) -> Optional[StoredRelationship]:
        """Return a relationship by id."""
        ...

    async def get_active_relationship(
        self,
        source_id: str,
        target_id: str,
        *,
        rel_type: str,
    ) -> Optional[StoredRelationship]:
        """Return the active (valid_until IS NULL) relationship for the (source, target, type) triple."""
        ...

    async def list_relationships_for_entity(
        self,
        entity_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> Sequence[StoredRelationship]:
        """List relationships incident to an entity (in either direction), optionally filtered by status."""
        ...

    async def save_relationship(self, rel: NewRelationship) -> StoredRelationship:
        """Insert a new relationship row; returns the stored row with id and timestamps."""
        ...

    async def update_relationship_status(
        self,
        rel_id: str,
        *,
        status: str,
    ) -> StoredRelationship:
        """Set the status column ('approved'/'rejected'/'pending'); returns the updated row."""
        ...

    async def update_relationship_weight(
        self,
        rel_id: str,
        *,
        weight: float,
    ) -> None:
        """Set the weight column."""
        ...

    async def expire_relationship(self, rel_id: str) -> None:
        """Mark a relationship expired by setting valid_until = now()."""
        ...

    async def list_pending_relationships(
        self,
        *,
        rel_type: Optional[str] = None,
        limit: int = 100,
    ) -> Sequence[PendingRelationshipRow]:
        """Pending relationships joined with source/target entities for review."""
        ...

    async def save_rejected_pattern(
        self,
        source_name: str,
        target_name: str,
        rel_type: str,
        *,
        source_memory_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Idempotent UPSERT into rejected_patterns by (source_name, target_name, rel_type)."""
        ...

    # Traversal / stats
    async def query_relationships(
        self,
        entity_ids: Sequence[str],
        *,
        direction: str = "both",
        active_only: bool = True,
        at_time: Optional[datetime] = None,
        rel_types: Optional[Sequence[str]] = None,
    ) -> Sequence[StoredRelationship]:
        """Hop query for graph traversal. direction in {'inbound','outbound','both'}."""
        ...

    async def get_graph_stats(
        self,
        *,
        project: Optional[str] = None,
    ) -> GraphStats:
        """Aggregate graph statistics; optional project scope."""
        ...

    async def get_timeline_buckets(
        self,
        *,
        trunc: str,
        project: Optional[str] = None,
    ) -> Sequence[TimelineBucketRow]:
        """Memory creation buckets by date_trunc interval; trunc must be validated by caller."""
        ...

    async def get_memories_by_entities(
        self,
        entity_ids: Sequence[str],
        *,
        exclude_memory_id: Optional[str] = None,
        limit: int = 20,
    ) -> Sequence[StoredMemory]:
        """Memories that mention any of the given entity ids, ordered by created_at DESC."""
        ...

    async def search_memories_text(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> Sequence[StoredMemory]:
        """Case-insensitive substring match against memories.content for the UI search box."""
        ...

    # ── PolicyOps ────────────────────────────────────────────────────

    async def get_profile(self, profile_id: str) -> Optional[StoredProfile]:
        """Return a profile by id, or None if absent."""
        ...

    async def get_profile_by_name(self, org_id: str, name: str) -> Optional[StoredProfile]:
        """Return the profile matching (org_id, name), or None if absent."""
        ...

    async def list_profiles(self, org_id: str) -> Sequence[StoredProfile]:
        """List all profiles for an org, ordered by name."""
        ...

    async def create_profile(self, profile: NewProfile) -> StoredProfile:
        """Insert a new profile; returns the stored row with server-generated id/timestamps."""
        ...

    async def update_profile(self, profile_id: str, patch: ProfilePatch) -> Optional[StoredProfile]:
        """Apply a patch to a profile and return the updated row, or None if absent."""
        ...

    async def delete_profile(self, profile_id: str, org_id: str) -> bool:
        """Delete a profile; returns True if a row was deleted."""
        ...

    async def resolve_profile_for_key(self, org_id: str, name: str) -> Optional[StoredProfile]:
        """Resolve the effective profile for an org/name key, falling back to defaults."""
        ...

    # ── WorkspaceOps ─────────────────────────────────────────────────

    async def get_workspace(self, workspace_id: str, org_id: str) -> Optional[StoredWorkspace]:
        """Return a workspace by id within an org, or None if absent."""
        ...

    async def list_workspaces(self, org_id: str, *, include_archived: bool = False) -> Sequence[StoredWorkspace]:
        """List workspaces for an org; archived ones excluded by default."""
        ...

    async def create_workspace(self, ws: NewWorkspace) -> StoredWorkspace:
        """Insert a new workspace; returns the stored row with server-generated id/timestamps."""
        ...

    async def update_workspace(self, workspace_id: str, org_id: str, patch: WorkspacePatch) -> Optional[StoredWorkspace]:
        """Apply a patch to a workspace and return the updated row, or None if absent."""
        ...

    async def archive_workspace(self, workspace_id: str, org_id: str) -> bool:
        """Mark a workspace as archived; returns True if a row was updated."""
        ...

    async def add_workspace_member(self, member: NewMember) -> StoredMember:
        """Add a member to a workspace; returns the stored row with server-generated id/timestamps."""
        ...

    async def list_workspace_members(self, workspace_id: str) -> Sequence[StoredMember]:
        """List all members of a workspace, ordered by joined_at."""
        ...

    async def update_workspace_member_role(self, workspace_id: str, user_id: str, role: str) -> Optional[StoredMember]:
        """Update a member's role in a workspace; returns the updated row, or None if absent."""
        ...

    async def remove_workspace_member(self, workspace_id: str, user_id: str) -> bool:
        """Remove a member from a workspace; returns True if a row was deleted."""
        ...

    # ── AuthOps ──────────────────────────────────────────────────────

    async def get_api_key(self, key_id: str) -> Optional[StoredApiKey]:
        """Return an API key by id, or None if absent."""
        ...

    async def list_api_keys(self, org_id: str) -> Sequence[StoredApiKey]:
        """List all API keys for an org, ordered by created_at DESC."""
        ...

    async def create_api_key(self, key: NewApiKey) -> StoredApiKey:
        """Insert a new API key; returns the stored row with server-generated id/timestamps."""
        ...

    async def revoke_api_key(self, key_id: str) -> Optional[StoredApiKey]:
        """Revoke an API key; returns the updated row, or None if absent."""
        ...

    async def count_active_root_keys(self, org_id: str) -> int:
        """Count active (non-revoked) root-level API keys for an org."""
        ...

    # ── AnalyticsOps ─────────────────────────────────────────────────

    async def record_retrieval_event(self, event: NewRetrievalEvent) -> None:
        """Persist a retrieval analytics event row."""
        ...

    async def record_memory_access(self, org_id: str, memory_id: str) -> Optional[StoredMemory]:
        """Increment access counters and return the updated memory, or None if absent."""
        ...

    async def list_recent_session_snapshots(
        self,
        org_id: str,
        *,
        project: Optional[str] = None,
        exclude_ids: Sequence[str] = (),
        limit: int = 3,
    ) -> Sequence[StoredMemory]:
        """List the most recent session-snapshot memories for an org, optionally scoped to a project."""
        ...

    async def compute_retrieval_analytics(
        self,
        *,
        org_id: str,
        days: int,
        project: Optional[str] = None,
    ) -> RetrievalAnalyticsResult:
        """Compute aggregated retrieval analytics for an org over the given number of days."""
        ...

    # ── RecommendationOps ────────────────────────────────────────────

    async def get_recommendation_config(
        self,
        *,
        workspace_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[StoredRecommendationConfig]:
        """Return the recommendation config for the given workspace/agent scope, or None if absent."""
        ...

    async def upsert_recommendation_config(
        self,
        *,
        workspace_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        aggressiveness: Optional[float] = None,
        enabled: Optional[bool] = None,
        max_suggestions: Optional[int] = None,
        cooldown_minutes: Optional[int] = None,
    ) -> StoredRecommendationConfig:
        """Insert or update the recommendation config for the given scope; returns the stored row."""
        ...

    async def record_recommendation_feedback(
        self, feedback: NewRecommendationFeedback,
    ) -> None:
        """Persist a recommendation feedback row (thumbs-up/down, dismissed, etc.)."""
        ...

    async def list_candidate_memories_for_recommendation(
        self, org_id: str, *, limit: int = 500,
    ) -> Sequence[RecommendationCandidate]:
        """List memory candidates for the recommendation engine, ordered by recency."""
        ...

    # ── ConversationOps ──────────────────────────────────────────────

    async def create_conversation_job(self, job: NewConversationJob) -> StoredConversationJob:
        """Insert a new conversation processing job; returns the stored row with server-generated id/timestamps."""
        ...

    async def get_conversation_job(
        self, job_id: str, org_id: str,
    ) -> Optional[StoredConversationJob]:
        """Return a conversation job by id within an org, or None if absent."""
        ...

    async def mark_conversation_job_processing(
        self, job_id: str,
    ) -> Optional[StoredConversationJob]:
        """Transition a job to processing status; returns the updated row, or None if absent."""
        ...

    async def complete_conversation_job(
        self,
        job_id: str,
        *,
        memory_ids: Sequence[str],
        memories_extracted: int,
        duplicates_skipped: int,
        processing_time_ms: int,
    ) -> None:
        """Mark a job completed and record its extraction results."""
        ...

    async def fail_conversation_job(
        self,
        job_id: str,
        *,
        error: str,
        processing_time_ms: int,
    ) -> None:
        """Mark a job failed and record the error message."""
        ...

    # ── AuditOps ─────────────────────────────────────────────────────

    async def query_audit_log(
        self,
        *,
        org_id: str,
        workspace_id: Optional[str] = None,
        action: Optional[str] = None,
        actor_id: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 50,
    ) -> Sequence[StoredAuditEntry]:
        """Query the audit log for an org with optional filters; returns entries newest-first."""
        ...
