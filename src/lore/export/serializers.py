"""Bidirectional dataclass ↔ dict serialization for export/import.

All conversion functions are pure — no I/O, no side effects.
Embedding serialization uses base64 encoding of raw bytes.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from typing import Any, Dict, List, Optional

from lore.types import (
    ConflictEntry,
    ConsolidationLogEntry,
    Entity,
    EntityMention,
    Fact,
    Memory,
    Relationship,
)

# ── Memory ──────────────────────────────────────────────────────────

def memory_to_dict(memory: Memory, include_embedding: bool = False) -> Dict[str, Any]:
    """Convert a Memory to a JSON-serializable dict."""
    d: Dict[str, Any] = {
        "id": memory.id,
        "content": memory.content,
        "type": memory.type,
        "tier": memory.tier,
        "context": memory.context,
        "tags": list(memory.tags) if memory.tags else [],
        "metadata": memory.metadata,
        "source": memory.source,
        "project": memory.project,
        "embedding": None,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
        "ttl": memory.ttl,
        "expires_at": memory.expires_at,
        "confidence": memory.confidence,
        "upvotes": memory.upvotes,
        "downvotes": memory.downvotes,
        "importance_score": memory.importance_score,
        "access_count": memory.access_count,
        "last_accessed_at": memory.last_accessed_at,
        "archived": memory.archived,
        "consolidated_into": memory.consolidated_into,
    }
    if include_embedding and memory.embedding is not None:
        d["embedding"] = serialize_embedding(memory.embedding)
    return d


def dict_to_memory(d: Dict[str, Any]) -> Memory:
    """Reconstruct a Memory from a dict, applying defaults for missing fields."""
    embedding = None
    if d.get("embedding") is not None:
        embedding = deserialize_embedding(d["embedding"])
    return Memory(
        id=d["id"],
        content=d["content"],
        type=d.get("type", "general"),
        tier=d.get("tier", "long"),
        context=d.get("context"),
        tags=list(d.get("tags") or []),
        metadata=d.get("metadata"),
        source=d.get("source"),
        project=d.get("project"),
        embedding=embedding,
        created_at=d.get("created_at", ""),
        updated_at=d.get("updated_at", ""),
        ttl=d.get("ttl"),
        expires_at=d.get("expires_at"),
        confidence=d.get("confidence", 1.0),
        upvotes=d.get("upvotes", 0),
        downvotes=d.get("downvotes", 0),
        importance_score=d.get("importance_score", 1.0),
        access_count=d.get("access_count", 0),
        last_accessed_at=d.get("last_accessed_at"),
        archived=d.get("archived", False),
        consolidated_into=d.get("consolidated_into"),
    )


# ── Entity ──────────────────────────────────────────────────────────

def entity_to_dict(entity: Entity) -> Dict[str, Any]:
    return {
        "id": entity.id,
        "name": entity.name,
        "entity_type": entity.entity_type,
        "aliases": list(entity.aliases) if entity.aliases else [],
        "description": entity.description,
        "metadata": entity.metadata,
        "mention_count": entity.mention_count,
        "first_seen_at": entity.first_seen_at,
        "last_seen_at": entity.last_seen_at,
        "created_at": entity.created_at,
        "updated_at": entity.updated_at,
    }


def dict_to_entity(d: Dict[str, Any]) -> Entity:
    return Entity(
        id=d["id"],
        name=d["name"],
        entity_type=d["entity_type"],
        aliases=list(d.get("aliases") or []),
        description=d.get("description"),
        metadata=d.get("metadata"),
        mention_count=d.get("mention_count", 1),
        first_seen_at=d.get("first_seen_at", ""),
        last_seen_at=d.get("last_seen_at", ""),
        created_at=d.get("created_at", ""),
        updated_at=d.get("updated_at", ""),
    )


# ── Relationship ────────────────────────────────────────────────────

def relationship_to_dict(rel: Relationship) -> Dict[str, Any]:
    return {
        "id": rel.id,
        "source_entity_id": rel.source_entity_id,
        "target_entity_id": rel.target_entity_id,
        "rel_type": rel.rel_type,
        "weight": rel.weight,
        "properties": rel.properties,
        "source_fact_id": rel.source_fact_id,
        "source_memory_id": rel.source_memory_id,
        "valid_from": rel.valid_from,
        "valid_until": rel.valid_until,
        "created_at": rel.created_at,
        "updated_at": rel.updated_at,
    }


def dict_to_relationship(d: Dict[str, Any]) -> Relationship:
    return Relationship(
        id=d["id"],
        source_entity_id=d["source_entity_id"],
        target_entity_id=d["target_entity_id"],
        rel_type=d["rel_type"],
        weight=d.get("weight", 1.0),
        properties=d.get("properties"),
        source_fact_id=d.get("source_fact_id"),
        source_memory_id=d.get("source_memory_id"),
        valid_from=d.get("valid_from", ""),
        valid_until=d.get("valid_until"),
        created_at=d.get("created_at", ""),
        updated_at=d.get("updated_at", ""),
    )


# ── EntityMention ───────────────────────────────────────────────────

def entity_mention_to_dict(mention: EntityMention) -> Dict[str, Any]:
    return {
        "id": mention.id,
        "entity_id": mention.entity_id,
        "memory_id": mention.memory_id,
        "mention_type": mention.mention_type,
        "confidence": mention.confidence,
        "created_at": mention.created_at,
    }


def dict_to_entity_mention(d: Dict[str, Any]) -> EntityMention:
    return EntityMention(
        id=d["id"],
        entity_id=d["entity_id"],
        memory_id=d["memory_id"],
        mention_type=d.get("mention_type", "explicit"),
        confidence=d.get("confidence", 1.0),
        created_at=d.get("created_at", ""),
    )


# ── Fact ────────────────────────────────────────────────────────────

def fact_to_dict(fact: Fact) -> Dict[str, Any]:
    return {
        "id": fact.id,
        "memory_id": fact.memory_id,
        "subject": fact.subject,
        "predicate": fact.predicate,
        "object": fact.object,
        "confidence": fact.confidence,
        "extracted_at": fact.extracted_at,
        "invalidated_by": fact.invalidated_by,
        "invalidated_at": fact.invalidated_at,
        "metadata": fact.metadata,
    }


def dict_to_fact(d: Dict[str, Any]) -> Fact:
    return Fact(
        id=d["id"],
        memory_id=d["memory_id"],
        subject=d["subject"],
        predicate=d["predicate"],
        object=d["object"],
        confidence=d.get("confidence", 1.0),
        extracted_at=d.get("extracted_at", ""),
        invalidated_by=d.get("invalidated_by"),
        invalidated_at=d.get("invalidated_at"),
        metadata=d.get("metadata"),
    )


# ── ConflictEntry ───────────────────────────────────────────────────

def conflict_to_dict(entry: ConflictEntry) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "new_memory_id": entry.new_memory_id,
        "old_fact_id": entry.old_fact_id,
        "new_fact_id": entry.new_fact_id,
        "subject": entry.subject,
        "predicate": entry.predicate,
        "old_value": entry.old_value,
        "new_value": entry.new_value,
        "resolution": entry.resolution,
        "resolved_at": entry.resolved_at,
        "metadata": entry.metadata,
    }


def dict_to_conflict(d: Dict[str, Any]) -> ConflictEntry:
    return ConflictEntry(
        id=d["id"],
        new_memory_id=d["new_memory_id"],
        old_fact_id=d["old_fact_id"],
        new_fact_id=d.get("new_fact_id"),
        subject=d["subject"],
        predicate=d["predicate"],
        old_value=d["old_value"],
        new_value=d["new_value"],
        resolution=d["resolution"],
        resolved_at=d["resolved_at"],
        metadata=d.get("metadata"),
    )


# ── ConsolidationLogEntry ───────────────────────────────────────────

def consolidation_log_to_dict(entry: ConsolidationLogEntry) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "consolidated_memory_id": entry.consolidated_memory_id,
        "original_memory_ids": list(entry.original_memory_ids),
        "strategy": entry.strategy,
        "model_used": entry.model_used,
        "original_count": entry.original_count,
        "created_at": entry.created_at,
        "metadata": entry.metadata,
    }


def dict_to_consolidation_log(d: Dict[str, Any]) -> ConsolidationLogEntry:
    return ConsolidationLogEntry(
        id=d["id"],
        consolidated_memory_id=d["consolidated_memory_id"],
        original_memory_ids=list(d.get("original_memory_ids") or []),
        strategy=d["strategy"],
        model_used=d.get("model_used"),
        original_count=d.get("original_count", 0),
        created_at=d.get("created_at", ""),
        metadata=d.get("metadata"),
    )


# ── Embedding helpers ───────────────────────────────────────────────

def serialize_embedding(data: bytes) -> str:
    """Encode raw embedding bytes as base64 string."""
    return base64.b64encode(data).decode("ascii")


def deserialize_embedding(b64: str) -> bytes:
    """Decode base64 string back to raw embedding bytes."""
    return base64.b64decode(b64)


# ── Filename helpers ────────────────────────────────────────────────

_SAFE_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"[\s]+")


def slugify(text: str, max_length: int = 60) -> str:
    """Convert text to a filesystem-safe slug."""
    text = unicodedata.normalize("NFKD", text)
    # Keep only word chars, whitespace, hyphens
    text = _SAFE_RE.sub("", text)
    text = _WHITESPACE_RE.sub("-", text.strip())
    text = text.strip("-").lower()
    if len(text) > max_length:
        text = text[:max_length].rstrip("-")
    return text or "untitled"


def memory_to_filename(memory: Memory) -> str:
    """Generate a filesystem-safe filename from a memory.

    Format: ``<id_prefix>-<slug>.md``  (max 200 chars total).
    """
    id_prefix = memory.id[:12]
    slug = slugify(memory.content[:120])
    name = f"{id_prefix}-{slug}"
    if len(name) > 195:
        name = name[:195]
    return f"{name}.md"
