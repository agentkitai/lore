"""Server-side Store Protocol.

The Store is the only place in the codebase that touches raw SQL or DB drivers.
Routes and services call typed methods declared here. Phase 1A defines the
MemoryOps slice; later phases extend the protocol with GraphOps, WorkspaceOps,
SnapshotOps, AnalyticsOps, PolicyOps, AuthOps, etc.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol, Sequence, runtime_checkable

from lore.persistence.types import (
    GraphStats,
    MemoryFilter,
    MemoryPatch,
    NewApiKey,
    NewEntity,
    NewMemory,
    NewMember,
    NewMention,
    NewProfile,
    NewRelationship,
    NewWorkspace,
    PendingRelationshipRow,
    ProfilePatch,
    RecallParams,
    ScoredMemory,
    StoredApiKey,
    StoredEntity,
    StoredMember,
    StoredMemory,
    StoredMention,
    StoredProfile,
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
