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
    AgentSharingConfigData,
    AuditEventData,
    DenyListRuleData,
    DreamRun,
    ExportedMemory,
    GraphStats,
    MemoryFilter,
    MemoryPatch,
    NewApiKey,
    NewAuditEvent,
    NewConversationJob,
    NewDenyListRule,
    NewDreamRun,
    NewDrillResult,
    NewEntity,
    NewMember,
    NewMemory,
    NewMention,
    NewProfile,
    NewRecommendationFeedback,
    NewRelationship,
    NewRetentionPolicy,
    NewRetrievalEvent,
    NewSloAlert,
    NewSloDefinition,
    NewWorkspace,
    PendingRelationshipRow,
    ProfilePatch,
    RecallParams,
    RecommendationCandidate,
    RetentionPolicyPatch,
    RetrievalAnalyticsResult,
    ScoredMemory,
    SharingConfigData,
    SharingConfigPatch,
    SharingStatsData,
    SloDefinitionPatch,
    StoredApiKey,
    StoredAuditEntry,
    StoredConversationJob,
    StoredDrillResult,
    StoredEntity,
    StoredMember,
    StoredMemory,
    StoredMention,
    StoredProfile,
    StoredRecommendationConfig,
    StoredRelationship,
    StoredRelationshipSupersession,
    StoredRetentionPolicy,
    StoredSloAlert,
    StoredSloDefinition,
    StoredSnapshotMetadata,
    StoredSupersession,
    StoredWorkspace,
    TimelineBucketRow,
    TimeseriesPoint,
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

    async def get_memory(
        self, org_id: str, memory_id: str, *, requesting_user_id: Optional[str] = None
    ) -> Optional[StoredMemory]:
        """Return a memory by id within an org, or None if absent or expired.

        Migration 026: when ``requesting_user_id`` is set, another user's
        private row is treated as absent (returns None). None = unfiltered
        (internal callers / solo mode), preserving prior behavior.
        """
        ...

    async def update_memory(
        self,
        org_id: str,
        memory_id: str,
        patch: MemoryPatch,
        *,
        requesting_user_id: Optional[str] = None,
    ) -> StoredMemory:
        """Apply a patch and return the updated row. Raises StoreNotFoundError if missing.

        Migration 026: when ``requesting_user_id`` is set, a row owned by a
        different user is treated as absent (raises StoreNotFoundError) — so a
        caller cannot patch another principal's private memory. None = unfiltered
        (internal callers / solo mode), preserving prior behavior.
        """
        ...

    async def delete_memory(
        self, org_id: str, memory_id: str, *, requesting_user_id: Optional[str] = None
    ) -> bool:
        """Delete a memory; returns True if a row was deleted.

        Migration 026: when ``requesting_user_id`` is set, a row owned by a
        different user is not deleted (returns False). None = unfiltered.
        """
        ...

    async def promote_memory(
        self, org_id: str, memory_id: str, *, promoted_by: Optional[str]
    ) -> Optional[StoredMemory]:
        """Migration 026: flip a PRIVATE memory to SHARED, recording who/when.

        Owner-gated when ``promoted_by`` is set (only the owner may share
        their own private row); unconstrained in solo mode (``promoted_by``
        None). Returns the updated row, or None if nothing matched (not found
        / already shared / not owned by the promoter).
        """
        ...

    async def demote_memory(
        self, org_id: str, memory_id: str, *, demoted_by: Optional[str]
    ) -> Optional[StoredMemory]:
        """Migration 026: flip a SHARED memory back to PRIVATE (clears promote
        provenance). Owner-gated symmetrically with ``promote_memory``."""
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
        """Increment access_count + last_accessed_at."""
        ...

    async def vote_memory(
        self,
        org_id: str,
        memory_id: str,
        *,
        direction: str,
        requesting_user_id: Optional[str] = None,
    ) -> StoredMemory:
        """direction is 'up' or 'down'. Returns the updated memory.

        Migration 026: when ``requesting_user_id`` is set, voting is gated by
        READ visibility — a caller may vote on shared/own/unowned memories but
        not on another principal's private row (raises StoreNotFoundError).
        None = unfiltered (internal/solo).
        """
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

    # ── SupersessionOps (Phase 6F) ───────────────────────────────────

    async def record_supersession(
        self,
        memory_id: str,
        *,
        superseded_by: Optional[str],
        reason: Optional[str],
        agent: str = "auto",
    ) -> None:
        """Append a row to the ``memory_supersessions`` audit log.

        ``superseded_by`` may be ``None`` to explicitly un-supersede a
        previously-superseded memory; the row stays in the audit trail.
        """
        ...

    async def is_superseded(
        self,
        memory_id: str,
        *,
        at: Optional[datetime] = None,
    ) -> bool:
        """True iff the memory's LATEST ``memory_supersessions`` row before ``at``
        (default ``now``) has ``superseded_by IS NOT NULL``."""
        ...

    async def are_superseded(
        self,
        memory_ids: "set[str]",
        *,
        at: Optional[datetime] = None,
    ) -> "set[str]":
        """Batch helper: return the subset of ``memory_ids`` that are
        superseded as of ``at`` (default ``now``).

        Used by the hybrid-recall pipeline to score-suppress superseded
        memories in a single round-trip.
        """
        ...

    async def get_supersession_chain(
        self,
        memory_id: str,
    ) -> Sequence[StoredSupersession]:
        """Full audit trail for a memory, ordered oldest-first."""
        ...

    async def list_supersession_sources(
        self,
        memory_id: str,
    ) -> Sequence[StoredSupersession]:
        """Inverse of get_supersession_chain: rows where superseded_by=memory_id.

        Returns each event whose ``superseded_by`` equals ``memory_id`` —
        i.e. the source memories that this memory consolidates / replaces.
        Used by the provenance endpoint so a caller can ask "where did
        this memory come from?" and get a typed answer.
        """
        ...

    async def list_memories_at_time(
        self,
        org_id: str,
        *,
        at: datetime,
        entity_name: Optional[str] = None,
        type_filter: Optional[str] = None,
        limit: int = 20,
        requesting_user_id: Optional[str] = None,
    ) -> Sequence[StoredMemory]:
        """Memories created on or before ``at`` and not superseded as of ``at``.

        Optional ``entity_name`` filters via ``entity_mentions``; optional
        ``type_filter`` matches ``meta->>'type'``.
        """
        ...

    async def list_timeline_around(
        self,
        *,
        anchor_id: str,
        org_id: str,
        direction: str,
        limit: int,
        max_hours: float,
    ) -> tuple[Optional[StoredMemory], list[StoredMemory]]:
        """Phase 6G — return ``(anchor, adjacent rows)`` where adjacent rows
        are same-project as the anchor, within ±``max_hours`` of
        ``anchor.created_at``, ordered by ``created_at`` ASC.

        Returns ``(None, [])`` if the anchor is not found or its
        ``org_id`` does not match the caller. ``direction`` is
        ``'before'`` | ``'after'`` | ``'both'``; for ``'both'`` the limit
        splits as ``before = ceil(limit/2)`` (most-recent-N before
        the anchor, then reversed to ASC) and ``after = floor(limit/2)``
        (oldest-N after, ASC). The anchor itself is excluded from
        the adjacent list.
        """
        ...

    # ── GraphOps ─────────────────────────────────────────────────────

    # Entity ops
    async def get_entity(self, entity_id: str) -> Optional[StoredEntity]:
        """Return an entity by id, or None if absent."""
        ...

    async def find_entity_by_name_or_alias(
        self, name: str,
    ) -> Optional[StoredEntity]:
        """Case-insensitive lookup matching ``name`` or any alias.

        Used by the graph-extraction service to dedupe when the LLM emits
        a name like ``"pinecone"`` and an entity already exists with
        canonical name ``"Pinecone"`` or alias ``"PC"``. Behavior:

        * ``LOWER(name) = LOWER(?)`` matches first.
        * Falls back to ``? IN aliases`` (case-insensitive).

        The exact-match ``get_entity_by_name`` and the upsert-by-name
        ``upsert_entity`` paths are unchanged — services that already
        normalize at the boundary keep their existing semantics.
        """
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

    async def replace_memory_mentions(
        self,
        memory_id: str,
        mentions: Sequence[NewMention],
    ) -> int:
        """Delete every existing mention for ``memory_id``, then insert
        the supplied set. Used by the graph-extraction service so a
        re-extraction of the same memory rewrites its edges atomically
        without leaving stale rows. Returns the count of inserted rows.
        """
        ...

    async def list_memories_without_mentions(
        self,
        org_id: str,
        *,
        project: Optional[str] = None,
        limit: int = 1000,
    ) -> Sequence[StoredMemory]:
        """Memories with zero rows in ``entity_mentions``. Drives the
        backfill endpoint: only memories that haven't been processed by
        the graph-extraction pipeline are returned. Newest first so
        recent activity gets the graph populated quickest.
        """
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

    async def replace_memory_relationships(
        self,
        memory_id: str,
        relationships: Sequence[NewRelationship],
    ) -> int:
        """Delete every relationship with ``source_memory_id = memory_id``
        and insert the supplied set. Active-edge UNIQUE conflicts (same
        ``source_entity_id`` / ``target_entity_id`` / ``rel_type`` already
        present from another memory) are silently skipped — those edges
        already exist; we don't double-count from a different source
        memory. Returns the count of inserted rows.
        """
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

    # ── Relationship supersession (bi-temporal facts, #67) ──────────────
    # Relationship edges (subject–predicate–object) ARE Lore's durable facts.
    # These mirror the memory SupersessionOps for supersede-not-delete + an
    # auditable correction chain at the edge level.

    async def supersede_relationship(
        self,
        relationship_id: str,
        *,
        superseded_by: str,
        reason: Optional[str] = None,
        agent: str = "auto",
    ) -> None:
        """Supersede-not-delete: close ``relationship_id``'s validity window
        (``valid_until = now``), point its ``superseded_by`` at the newer edge,
        and append the correction to ``relationship_supersessions`` — atomically.
        ``query_relationships(at_time=...)`` then excludes it as of now while
        as-of-past-date queries still return it."""
        ...

    async def record_relationship_supersession(
        self,
        relationship_id: str,
        *,
        superseded_by: Optional[str],
        reason: Optional[str],
        agent: str = "auto",
    ) -> None:
        """Append a row to ``relationship_supersessions`` WITHOUT touching the
        edge's validity window (bare primitive; parity with
        ``record_supersession``). Prefer ``supersede_relationship``."""
        ...

    async def is_relationship_superseded(
        self,
        relationship_id: str,
        *,
        at: Optional[datetime] = None,
    ) -> bool:
        """True iff the edge's LATEST ``relationship_supersessions`` row before
        ``at`` (default ``now``) has ``superseded_by IS NOT NULL``."""
        ...

    async def get_relationship_supersession_chain(
        self,
        relationship_id: str,
    ) -> Sequence[StoredRelationshipSupersession]:
        """Full correction trail for an edge, ordered oldest-first."""
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

    async def recall_by_text(
        self,
        org_id: str,
        query: str,
        *,
        limit: int = 20,
        project: Optional[str] = None,
        scope_mode: str = "default",
        requesting_user_id: Optional[str] = None,
    ) -> Sequence[tuple[StoredMemory, float]]:
        """Full-text search with backend-native ranking.

        Phase 6C hybrid retrieval: PG uses ``ts_rank`` against the GIN index
        introduced in 020_fts_index.sql; SQLite uses ``bm25(memories_fts)``
        against the FTS5 virtual table.

        Phase 6G: ``scope_mode`` controls the project-vs-global predicate —
        ``'default'`` applies
        ``(scope='global') OR (scope='project' AND project=:current)``,
        ``'all'`` skips it.

        Returns ``[(memory, fts_rank)]`` ordered by descending rank. Empty
        when the query yields no terms or the FTS migration hasn't been
        applied.
        """
        ...

    async def recall_by_entities(
        self,
        org_id: str,
        entity_ids: Sequence[str],
        *,
        limit: int = 20,
        project: Optional[str] = None,
        scope_mode: str = "default",
        requesting_user_id: Optional[str] = None,
    ) -> Sequence[tuple[StoredMemory, int]]:
        """Memories tied to any of the given entities, ranked by mention count.

        Phase 6C hybrid retrieval: companion to the existing
        ``get_memories_by_entities`` but returns the count of overlapping
        entity ids per memory so the service layer can use it as a graph
        signal in RRF fusion.

        Phase 6G: ``scope_mode`` + ``project`` mirror ``recall_by_embedding``.
        """
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

    async def lookup_api_key_by_hash(self, key_hash: str) -> Optional[StoredApiKey]:
        """Return the API key matching a sha256 key_hash, or None if absent.

        Used by the auth middleware on every request (after cache miss)."""
        ...

    async def touch_api_key_last_used(self, key_id: str) -> None:
        """Update last_used_at = now() for an API key.

        Called from a debounced background task — failure is logged, not raised."""
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
        requesting_user_id: Optional[str] = None,
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

    async def compute_metric_value(
        self, *, org_id: str, metric: str, window_minutes: int,
    ) -> Optional[float]: ...

    async def compute_metric_timeseries(
        self, *, org_id: str, metric: str, window_hours: int, bucket_minutes: int,
    ) -> Sequence[TimeseriesPoint]: ...

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
        self, org_id: str, *, limit: int = 500, requesting_user_id: Optional[str] = None,
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

    # ── RetentionOps ────────────────────────────────────────────────

    async def list_retention_policies(self, org_id: str) -> Sequence[StoredRetentionPolicy]: ...

    async def get_retention_policy(self, policy_id: str, org_id: str) -> Optional[StoredRetentionPolicy]: ...

    async def create_retention_policy(self, policy: NewRetentionPolicy) -> StoredRetentionPolicy: ...

    async def update_retention_policy(
        self, policy_id: str, org_id: str, patch: RetentionPolicyPatch,
    ) -> Optional[StoredRetentionPolicy]: ...

    async def delete_retention_policy(self, policy_id: str, org_id: str) -> bool: ...

    async def get_latest_snapshot_for_policy(
        self, policy_id: str, org_id: str,
    ) -> Optional[StoredSnapshotMetadata]: ...

    async def count_snapshots_for_policy(self, policy_id: str) -> int: ...

    async def record_drill_result(self, drill: NewDrillResult) -> StoredDrillResult: ...

    async def list_drill_results_for_policy(
        self, policy_id: str, org_id: str, *, limit: int = 20,
    ) -> Sequence[StoredDrillResult]: ...

    async def get_latest_drill_result(self, org_id: str) -> Optional[StoredDrillResult]: ...

    # ── SloOps ────────────────────────────────────────────────────────

    async def list_slo_definitions(self, org_id: Optional[str] = None) -> Sequence[StoredSloDefinition]: ...

    async def get_slo_definition(self, slo_id: str, org_id: str) -> Optional[StoredSloDefinition]: ...

    async def create_slo_definition(self, slo: NewSloDefinition) -> StoredSloDefinition: ...

    async def update_slo_definition(
        self, slo_id: str, org_id: str, patch: SloDefinitionPatch,
    ) -> Optional[StoredSloDefinition]: ...

    async def delete_slo_definition(self, slo_id: str, org_id: str) -> bool: ...

    async def list_slo_alerts(
        self, *, slo_id: Optional[str] = None, limit: int = 50,
    ) -> Sequence[StoredSloAlert]: ...

    async def record_slo_alert(self, alert: NewSloAlert) -> StoredSloAlert: ...

    # ── SharingOps ────────────────────────────────────────────────────

    async def get_or_init_sharing_config(self, org_id: str) -> SharingConfigData:
        """Return the sharing config for an org, creating a default row if missing."""
        ...

    async def update_sharing_config(
        self, org_id: str, patch: SharingConfigPatch,
    ) -> SharingConfigData:
        """Upsert + apply a patch to the sharing config; returns the updated row."""
        ...

    async def list_agent_sharing_configs(
        self, org_id: str,
    ) -> Sequence[AgentSharingConfigData]:
        """List per-agent sharing configs for an org, ordered by agent_id."""
        ...

    async def upsert_agent_sharing_config(
        self,
        org_id: str,
        agent_id: str,
        *,
        enabled: bool,
        categories: Sequence[str],
    ) -> AgentSharingConfigData:
        """Insert or update the sharing config for a (org, agent) pair."""
        ...

    async def list_deny_rules(self, org_id: str) -> Sequence[DenyListRuleData]:
        """List deny-list rules for an org, ordered by created_at."""
        ...

    async def create_deny_rule(self, rule: NewDenyListRule) -> DenyListRuleData:
        """Insert a new deny-list rule; returns the stored row."""
        ...

    async def delete_deny_rule(self, rule_id: str, org_id: str) -> bool:
        """Delete a deny-list rule scoped to an org; True if a row was removed."""
        ...

    async def list_audit_events(
        self,
        org_id: str,
        *,
        event_type: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 50,
    ) -> Sequence[AuditEventData]:
        """List sharing audit events for an org with optional filters; newest first."""
        ...

    async def record_audit_event(self, event: NewAuditEvent) -> None:
        """Persist a sharing audit event row."""
        ...

    async def get_sharing_stats(self, org_id: str) -> SharingStatsData:
        """Compute aggregate sharing stats: lessons count, last shared, audit summary."""
        ...

    async def purge_sharing(self, org_id: str) -> int:
        """Purge all sharing-related rows for an org in a single tx; returns deleted lessons count."""
        ...

    async def rate_lesson(
        self,
        lesson_id: str,
        org_id: str,
        delta: int,
        initiated_by: str,
    ) -> Optional[int]:
        """Atomically adjust a lesson's reputation_score and write an audit event.

        Returns the new reputation_score, or None if the lesson does not exist.
        """
        ...

    # ── DreamOps (Phase 6E) ──────────────────────────────────────────

    async def start_dream(self, run: NewDreamRun) -> DreamRun:
        """Insert a new dream-run row in ``running`` status; returns the stored row."""
        ...

    async def complete_dream(
        self, run_id: str, summary: Mapping[str, Any],
    ) -> None:
        """Mark a dream run as completed with the given summary blob."""
        ...

    async def fail_dream(self, run_id: str, error: str) -> None:
        """Mark a dream run as failed with the given error string."""
        ...

    async def get_last_dream_run(self, org_id: str) -> Optional[DreamRun]:
        """Return the most recent dream run for an org, or None if absent."""
        ...

    async def count_distinct_sessions_since(
        self, org_id: str, since: datetime,
    ) -> int:
        """Count distinct ``meta->>'session_id'`` values across memories created
        for an org since the given timestamp.

        Used by the eligibility check (≥5 distinct sessions since last dream).
        Memories without a ``session_id`` in their meta are ignored.
        """
        ...
