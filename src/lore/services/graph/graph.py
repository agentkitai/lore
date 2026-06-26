"""Graph visualization, search, stats, clusters, timeline services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional, Sequence

from lore.persistence import (
    GraphStats,
    MemoryFilter,
    Store,
    StoredMemory,
)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    kind: str  # 'memory' | 'entity'
    label: str
    type: str  # mtype for memories, entity_type for entities
    tier: Optional[str] = None
    project: Optional[str] = None
    tags: Optional[Sequence[str]] = None
    created_at: Optional[datetime] = None
    upvotes: Optional[int] = None
    downvotes: Optional[int] = None
    access_count: Optional[int] = None
    mention_count: Optional[int] = None
    aliases: Optional[Sequence[str]] = None
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    target: str
    rel_type: str
    weight: float = 1.0
    label: str = ""


@dataclass(frozen=True, slots=True)
class GraphCounts:
    total_memories: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    filtered_nodes: int = 0
    filtered_edges: int = 0


@dataclass(frozen=True, slots=True)
class GraphData:
    nodes: Sequence[GraphNode]
    edges: Sequence[GraphEdge]
    counts: GraphCounts


@dataclass(frozen=True, slots=True)
class SearchHit:
    id: str
    content: str  # first 200 chars
    type: str
    project: Optional[str]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SearchResults:
    results: Sequence[SearchHit]
    total: int


@dataclass(frozen=True, slots=True)
class ConnectedEntityRef:
    id: str
    name: str
    entity_type: str
    rel_type: str  # always 'mentions' for this endpoint


@dataclass(frozen=True, slots=True)
class ConnectedMemoryRef:
    id: str
    label: str  # first 60 chars
    type: str  # from meta.type
    rel_type: str  # always 'related_to' for this endpoint


@dataclass(frozen=True, slots=True)
class MemoryWithGraph:
    memory: StoredMemory
    connected_entities: Sequence[ConnectedEntityRef]
    connected_memories: Sequence[ConnectedMemoryRef]


VALID_CLUSTER_GROUPS = frozenset({"project", "type", "tier"})


@dataclass(frozen=True, slots=True)
class Cluster:
    id: str  # f"cluster_{label}"
    label: str
    group_by: str
    node_count: int
    node_ids: Sequence[str]


@dataclass(frozen=True, slots=True)
class ClusterResult:
    clusters: Sequence[Cluster]
    nodes: Sequence[GraphNode]
    edges: Sequence[GraphEdge]


VALID_TIMELINE_BUCKETS = frozenset({"hour", "day", "week", "month"})


@dataclass(frozen=True, slots=True)
class TimelineBucket:
    date: str  # ISO format key (yyyy-mm-dd or yyyy-mm-ddTHH:00 for hourly)
    count: int
    by_type: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class TimelineResult:
    buckets: Sequence[TimelineBucket]
    range_start: Optional[str]
    range_end: Optional[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _memory_label(content: str) -> str:
    """Build a short label from memory content (60-char cap, no newlines)."""
    content = content.replace("\n", " ")
    return (content[:60] + "...") if len(content) > 60 else content


def _memory_node(m: StoredMemory) -> GraphNode:
    meta = m.meta or {}
    mtype = meta.get("type", "general")
    mtier = meta.get("tier", "long")
    return GraphNode(
        id=m.id,
        kind="memory",
        label=_memory_label(m.content or ""),
        type=mtype,
        tier=mtier,
        project=m.project,
        tags=tuple(m.tags),
        created_at=m.created_at,
        upvotes=m.upvotes,
        downvotes=m.downvotes,
        access_count=m.access_count,
    )


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


async def get_graph_data(
    store: Store,
    *,
    project: Optional[str] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 1000,
    include_orphans: bool = True,
    org_id: str,
) -> GraphData:
    """Build a graph view: nodes (memories + entities) and edges (mentions + approved relationships).

    Orphan filtering is applied in Python after fetching. Mention fetching is
    N+1 per memory and is capped to the first 500 memories in the result set.

    Future optimization: add Store.list_mentions(memory_ids=...) to batch this.
    """
    # 1. Get total counts via stats (avoids a separate COUNT query).
    stats = await store.get_graph_stats(org_id, project=project)

    # 2. Fetch filtered memory subset.
    f = MemoryFilter(
        org_id=org_id,
        project=project,
        type=type,
        tier=tier,
        since=since,
        until=until,
        limit=limit,
    )
    memories = await store.list_memories(f)

    # 3. Fetch all entities (no limit in legacy SQL).
    entities = await store.list_entities(org_id, limit=10000)

    # 4. Build memory nodes.
    nodes: list[GraphNode] = []

    for m in memories:
        nodes.append(_memory_node(m))

    # 5. Build entity nodes.
    entity_ids = [e.id for e in entities]

    for e in entities:
        nodes.append(
            GraphNode(
                id=e.id,
                kind="entity",
                label=e.name,
                type=e.entity_type,
                mention_count=e.mention_count,
                aliases=tuple(e.aliases),
                first_seen_at=e.first_seen_at,
                last_seen_at=e.last_seen_at,
                created_at=e.created_at,
            )
        )

    # 6. Build edges: mentions (N+1, capped at 500).
    edges: list[GraphEdge] = []
    capped_memories = list(memories)[:500]

    for m in capped_memories:
        mentions = await store.get_mentions_for_memory(m.id, org_id)
        for mention in mentions:
            edges.append(
                GraphEdge(
                    source=m.id,
                    target=mention.entity_id,
                    rel_type="mentions",
                    weight=mention.confidence,
                    label="mentions",
                )
            )

    # 7. Build edges: approved active relationships among entity subset.
    if entity_ids:
        relationships = await store.query_relationships(
            entity_ids, org_id, direction="both", active_only=True
        )
        for rel in relationships:
            if rel.status == "approved":
                edges.append(
                    GraphEdge(
                        source=rel.source_entity_id,
                        target=rel.target_entity_id,
                        rel_type=rel.rel_type,
                        weight=rel.weight,
                        label=rel.rel_type,
                    )
                )

    # 8. Orphan filter in Python.
    if not include_orphans:
        connected_ids: set[str] = set()
        for edge in edges:
            connected_ids.add(edge.source)
            connected_ids.add(edge.target)
        nodes = [n for n in nodes if n.id in connected_ids]

    counts = GraphCounts(
        total_memories=stats.total_memories,
        total_entities=stats.total_entities,
        total_relationships=stats.total_relationships,
        filtered_nodes=len(nodes),
        filtered_edges=len(edges),
    )
    return GraphData(
        nodes=tuple(nodes),
        edges=tuple(edges),
        counts=counts,
    )


async def search_graph_memories(
    store: Store,
    query: str,
    *,
    org_id: str,
    limit: int = 20,
) -> SearchResults:
    """Full-text (substring) search over memories for the graph UI search box.

    Returns an empty result immediately if *query* is blank — mirrors the
    legacy route short-circuit.
    """
    if not query or not query.strip():
        return SearchResults(results=(), total=0)

    memories = await store.search_memories_text(org_id, query, limit=limit)

    hits = tuple(
        SearchHit(
            id=m.id,
            content=(m.content or "")[:200],
            type=(m.meta or {}).get("type", "general"),
            project=m.project,
            created_at=m.created_at,
        )
        for m in memories
    )
    return SearchResults(results=hits, total=len(hits))


async def get_memory_with_graph(
    store: Store,
    memory_id: str,
    *,
    org_id: str,
) -> Optional[MemoryWithGraph]:
    """Fetch a memory together with its connected entities and related memories.

    Returns None when the memory does not exist. org_id is required so the
    fetch is scoped to the caller's tenant.
    """
    memory = await store.get_memory(org_id, memory_id)
    if memory is None:
        return None

    # Connected entities via mention table.
    mentions = await store.get_mentions_for_memory(memory_id, org_id)
    entity_refs: list[ConnectedEntityRef] = []
    for mention in mentions:
        entity = await store.get_entity(mention.entity_id, org_id)
        if entity is not None:
            entity_refs.append(
                ConnectedEntityRef(
                    id=entity.id,
                    name=entity.name,
                    entity_type=entity.entity_type,
                    rel_type="mentions",
                )
            )

    # Related memories via shared entities.
    entity_ids = [m.entity_id for m in mentions]
    related_memories: list[ConnectedMemoryRef] = []
    if entity_ids:
        related = await store.get_memories_by_entities(
            org_id, entity_ids, exclude_memory_id=memory_id, limit=20
        )
        for rm in related:
            content = rm.content or ""
            label = _memory_label(content)
            related_memories.append(
                ConnectedMemoryRef(
                    id=rm.id,
                    label=label,
                    type=(rm.meta or {}).get("type", "general"),
                    rel_type="related_to",
                )
            )

    return MemoryWithGraph(
        memory=memory,
        connected_entities=tuple(entity_refs),
        connected_memories=tuple(related_memories),
    )


async def get_stats(
    store: Store,
    *,
    org_id: str,
    project: Optional[str] = None,
) -> GraphStats:
    """Delegate to store.get_graph_stats. Returns the typed GraphStats dataclass."""
    return await store.get_graph_stats(org_id, project=project)


async def get_clusters(
    store: Store,
    *,
    group_by: str = "project",
    project: Optional[str] = None,
    org_id: str,
) -> ClusterResult:
    """Group memories into clusters by project, type, or tier.

    Raises ValueError for unrecognised *group_by* values.
    """
    if group_by not in VALID_CLUSTER_GROUPS:
        raise ValueError(
            f"group_by must be one of {sorted(VALID_CLUSTER_GROUPS)}; got {group_by!r}"
        )
    memories = await store.list_memories(
        MemoryFilter(org_id=org_id, project=project, limit=10000)
    )
    nodes: list[GraphNode] = []
    groups: dict[str, list[str]] = {}

    for m in memories:
        meta = m.meta or {}
        mtype = meta.get("type", "general")
        mtier = meta.get("tier", "long")
        content = m.content or ""
        label = (content[:60] + "...") if len(content) > 60 else content
        label = label.replace("\n", " ")
        nodes.append(
            GraphNode(
                id=m.id,
                kind="memory",
                label=label,
                type=mtype,
                tier=mtier,
                project=m.project,
                tags=tuple(m.tags),
                created_at=m.created_at,
                upvotes=m.upvotes,
                downvotes=m.downvotes,
                access_count=m.access_count,
            )
        )
        if group_by == "type":
            key = mtype
        elif group_by == "tier":
            key = mtier
        else:
            key = m.project or "(no project)"
        groups.setdefault(key, []).append(m.id)

    clusters = tuple(
        Cluster(
            id=f"cluster_{cluster_label}",
            label=cluster_label,
            group_by=group_by,
            node_count=len(ids),
            node_ids=tuple(ids),
        )
        for cluster_label, ids in groups.items()
    )
    return ClusterResult(clusters=clusters, nodes=tuple(nodes), edges=())


async def get_timeline(
    store: Store,
    *,
    org_id: str,
    bucket: str = "day",
    project: Optional[str] = None,
) -> TimelineResult:
    """Return memory-creation timeline grouped by time bucket.

    Raises ValueError for unrecognised *bucket* values.
    """
    if bucket not in VALID_TIMELINE_BUCKETS:
        raise ValueError(
            f"bucket must be one of {sorted(VALID_TIMELINE_BUCKETS)}; got {bucket!r}"
        )
    rows = await store.get_timeline_buckets(org_id, trunc=bucket, project=project)
    if not rows:
        return TimelineResult(buckets=(), range_start=None, range_end=None)

    bucket_data: dict[str, dict[str, int]] = {}
    for r in rows:
        if bucket == "hour":
            key = r.bucket_date.strftime("%Y-%m-%dT%H:00")
        else:
            key = r.bucket_date.strftime("%Y-%m-%d")
        bucket_data.setdefault(key, {})[r.mem_type] = r.count

    sorted_keys = sorted(bucket_data.keys())
    buckets = tuple(
        TimelineBucket(
            date=k,
            count=sum(bucket_data[k].values()),
            by_type=dict(bucket_data[k]),
        )
        for k in sorted_keys
    )
    return TimelineResult(
        buckets=buckets,
        range_start=sorted_keys[0],
        range_end=sorted_keys[-1],
    )
