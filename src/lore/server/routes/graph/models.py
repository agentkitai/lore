"""Pydantic response models for graph visualization endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    id: str
    kind: str
    label: str
    type: str
    tier: Optional[str] = None
    project: Optional[str] = None
    importance: Optional[float] = None
    confidence: Optional[float] = None
    tags: Optional[List[str]] = None
    created_at: Optional[str] = None
    upvotes: Optional[int] = None
    downvotes: Optional[int] = None
    access_count: Optional[int] = None
    mention_count: Optional[int] = None
    aliases: Optional[List[str]] = None
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None


class GraphEdge(BaseModel):
    source: str
    target: str
    rel_type: str
    weight: float = 1.0
    label: str = ""


class GraphStats(BaseModel):
    total_memories: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    filtered_nodes: int = 0
    filtered_edges: int = 0


class GraphResponse(BaseModel):
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []
    stats: GraphStats = Field(default_factory=GraphStats)


class StatsResponse(BaseModel):
    total_memories: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    by_type: Dict[str, int] = {}
    by_project: Dict[str, int] = {}
    by_tier: Dict[str, int] = {}
    by_entity_type: Dict[str, int] = {}
    avg_importance: float = 0.0
    top_entities: List[Dict[str, Any]] = []
    recent_24h: int = 0
    recent_7d: int = 0
    oldest_memory: Optional[str] = None
    newest_memory: Optional[str] = None


class TimelineBucket(BaseModel):
    date: str
    count: int
    by_type: Dict[str, int] = {}


class TimelineResponse(BaseModel):
    buckets: List[TimelineBucket] = []
    range: Dict[str, Optional[str]] = {"start": None, "end": None}


class MemoryDetailResponse(BaseModel):
    id: str
    content: str
    type: str
    tier: str = "long"
    project: Optional[str] = None
    tags: List[str] = []
    importance_score: float = 1.0
    confidence: float = 1.0
    upvotes: int = 0
    downvotes: int = 0
    access_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    source: Optional[str] = None
    connected_entities: List[Dict[str, Any]] = []
    connected_memories: List[Dict[str, Any]] = []


class ClusterItem(BaseModel):
    id: str
    label: str
    group_by: str
    node_count: int
    node_ids: List[str] = []


class ClusterResponse(BaseModel):
    clusters: List[ClusterItem] = []
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []


class SearchResult(BaseModel):
    id: str
    content: str
    type: str
    project: Optional[str] = None
    score: float = 0.0
    created_at: str = ""


class SearchResponse(BaseModel):
    results: List[SearchResult] = []
    total: int = 0


class EntityDetailResponse(BaseModel):
    id: str
    name: str
    entity_type: str
    mention_count: int = 0
    aliases: List[str] = []
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    connected_memories: List[Dict[str, Any]] = []
    connected_entities: List[Dict[str, Any]] = []


class TopicListItem(BaseModel):
    entity_id: str
    name: str
    entity_type: str
    mention_count: int


class TopicListResponse(BaseModel):
    topics: List[TopicListItem] = []
