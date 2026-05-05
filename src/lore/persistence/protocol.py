"""Server-side Store Protocol.

The Store is the only place in the codebase that touches raw SQL or DB drivers.
Routes and services call typed methods declared here. Phase 1A defines the
MemoryOps slice; later phases extend the protocol with GraphOps, WorkspaceOps,
SnapshotOps, AnalyticsOps, PolicyOps, AuthOps, etc.
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from lore.persistence.types import (
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    ScoredMemory,
    StoredMemory,
)


@runtime_checkable
class Store(Protocol):
    """The Store protocol.

    Implementations: PostgresStore (Phase 1A), SqliteStore (Phase 3).
    Method groups are added incrementally; Phase 1A defines MemoryOps.
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
        """Apply a patch and return the updated row. Raises StoreNotFound if missing."""
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
