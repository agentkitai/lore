"""Tests for S1: Serializers — dataclass-to-dict conversion."""

from __future__ import annotations

import struct

import pytest

from lore.export.serializers import (
    conflict_to_dict,
    consolidation_log_to_dict,
    deserialize_embedding,
    dict_to_conflict,
    dict_to_consolidation_log,
    dict_to_entity,
    dict_to_entity_mention,
    dict_to_fact,
    dict_to_memory,
    dict_to_relationship,
    entity_mention_to_dict,
    entity_to_dict,
    fact_to_dict,
    memory_to_dict,
    memory_to_filename,
    relationship_to_dict,
    serialize_embedding,
    slugify,
)
from lore.types import (
    ConflictEntry,
    ConsolidationLogEntry,
    Entity,
    EntityMention,
    Fact,
    Memory,
    Relationship,
)


def _make_memory(**overrides) -> Memory:
    defaults = dict(
        id="mem-001",
        content="SQLite WAL mode fixes concurrency",
        type="code",
        tier="long",
        context="debugging session",
        tags=["sqlite", "concurrency"],
        metadata={"key": "value"},
        source="claude-code",
        project="lore",
        embedding=struct.pack("4f", 0.1, 0.2, 0.3, 0.4),
        created_at="2026-01-15T10:00:00Z",
        updated_at="2026-01-15T10:30:00Z",
        ttl=3600,
        expires_at="2026-01-15T11:00:00Z",
        confidence=0.95,
        upvotes=3,
        downvotes=1,
        importance_score=0.82,
        access_count=5,
        last_accessed_at="2026-01-15T10:30:00Z",
        archived=False,
        consolidated_into=None,
    )
    defaults.update(overrides)
    return Memory(**defaults)


class TestMemoryRoundtrip:
    def test_memory_to_dict_all_fields(self):
        m = _make_memory()
        d = memory_to_dict(m, include_embedding=True)
        assert d["id"] == "mem-001"
        assert d["content"] == "SQLite WAL mode fixes concurrency"
        assert d["type"] == "code"
        assert d["tier"] == "long"
        assert d["context"] == "debugging session"
        assert d["tags"] == ["sqlite", "concurrency"]
        assert d["metadata"] == {"key": "value"}
        assert d["source"] == "claude-code"
        assert d["project"] == "lore"
        assert d["embedding"] is not None  # base64
        assert d["created_at"] == "2026-01-15T10:00:00Z"
        assert d["confidence"] == 0.95
        assert d["upvotes"] == 3
        assert d["downvotes"] == 1
        assert d["importance_score"] == 0.82
        assert d["access_count"] == 5
        assert d["archived"] is False
        assert d["consolidated_into"] is None

    def test_dict_to_memory_all_fields(self):
        m = _make_memory()
        d = memory_to_dict(m, include_embedding=True)
        m2 = dict_to_memory(d)
        assert m2.id == m.id
        assert m2.content == m.content
        assert m2.type == m.type
        assert m2.tier == m.tier
        assert m2.context == m.context
        assert m2.tags == m.tags
        assert m2.metadata == m.metadata
        assert m2.source == m.source
        assert m2.project == m.project
        assert m2.embedding == m.embedding
        assert m2.confidence == m.confidence
        assert m2.upvotes == m.upvotes
        assert m2.downvotes == m.downvotes
        assert m2.importance_score == m.importance_score
        assert m2.access_count == m.access_count
        assert m2.archived == m.archived

    def test_memory_roundtrip(self):
        m = _make_memory()
        d = memory_to_dict(m, include_embedding=True)
        m2 = dict_to_memory(d)
        d2 = memory_to_dict(m2, include_embedding=True)
        assert d == d2

    def test_embeddings_excluded_by_default(self):
        m = _make_memory()
        d = memory_to_dict(m, include_embedding=False)
        assert d["embedding"] is None

    def test_null_fields_preserved(self):
        m = _make_memory(
            context=None, metadata=None, source=None, project=None,
            embedding=None, ttl=None, expires_at=None,
            last_accessed_at=None, consolidated_into=None,
        )
        d = memory_to_dict(m)
        assert d["context"] is None
        assert d["metadata"] is None
        assert d["source"] is None
        assert d["project"] is None
        assert d["embedding"] is None
        assert d["ttl"] is None
        assert d["expires_at"] is None
        assert d["last_accessed_at"] is None
        assert d["consolidated_into"] is None

    def test_empty_tags_serialized_as_array(self):
        m = _make_memory(tags=[])
        d = memory_to_dict(m)
        assert d["tags"] == []
        assert isinstance(d["tags"], list)

    def test_unicode_content(self):
        m = _make_memory(content="日本語テスト 🎉 عربي")
        d = memory_to_dict(m)
        m2 = dict_to_memory(d)
        assert m2.content == "日本語テスト 🎉 عربي"


class TestEntityRoundtrip:
    def test_entity_roundtrip(self):
        e = Entity(
            id="e1", name="SQLite", entity_type="tool",
            aliases=["sqlite3", "SQLite3"],
            description="A database",
            metadata={"wiki": "link"},
            mention_count=5,
            first_seen_at="2026-01-01T00:00:00Z",
            last_seen_at="2026-01-15T00:00:00Z",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-15T00:00:00Z",
        )
        d = entity_to_dict(e)
        e2 = dict_to_entity(d)
        assert e2.id == e.id
        assert e2.name == e.name
        assert e2.entity_type == e.entity_type
        assert e2.aliases == e.aliases
        assert e2.description == e.description
        assert e2.metadata == e.metadata
        assert e2.mention_count == e.mention_count
        d2 = entity_to_dict(e2)
        assert d == d2


class TestRelationshipRoundtrip:
    def test_relationship_roundtrip(self):
        r = Relationship(
            id="r1",
            source_entity_id="e1", target_entity_id="e2",
            rel_type="uses", weight=0.8,
            properties={"context": "db"},
            source_fact_id="f1", source_memory_id="m1",
            valid_from="2026-01-01T00:00:00Z", valid_until=None,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        d = relationship_to_dict(r)
        r2 = dict_to_relationship(d)
        assert r2.id == r.id
        assert r2.source_entity_id == r.source_entity_id
        assert r2.rel_type == r.rel_type
        assert r2.properties == r.properties
        d2 = relationship_to_dict(r2)
        assert d == d2


class TestEntityMentionRoundtrip:
    def test_entity_mention_roundtrip(self):
        em = EntityMention(
            id="em1", entity_id="e1", memory_id="m1",
            mention_type="explicit", confidence=0.9,
            created_at="2026-01-01T00:00:00Z",
        )
        d = entity_mention_to_dict(em)
        em2 = dict_to_entity_mention(d)
        assert em2.id == em.id
        assert em2.entity_id == em.entity_id
        assert em2.memory_id == em.memory_id
        d2 = entity_mention_to_dict(em2)
        assert d == d2


class TestFactRoundtrip:
    def test_fact_roundtrip(self):
        f = Fact(
            id="f1", memory_id="m1",
            subject="sqlite", predicate="uses", object="WAL mode",
            confidence=0.95, extracted_at="2026-01-01T00:00:00Z",
            invalidated_by=None, invalidated_at=None,
            metadata={"source": "auto"},
        )
        d = fact_to_dict(f)
        f2 = dict_to_fact(d)
        assert f2.id == f.id
        assert f2.subject == f.subject
        assert f2.metadata == f.metadata
        d2 = fact_to_dict(f2)
        assert d == d2


class TestConflictRoundtrip:
    def test_conflict_roundtrip(self):
        c = ConflictEntry(
            id="c1", new_memory_id="m2", old_fact_id="f1", new_fact_id="f2",
            subject="user", predicate="lives_in",
            old_value="NYC", new_value="Berlin",
            resolution="SUPERSEDE", resolved_at="2026-01-01T00:00:00Z",
            metadata={"reasoning": "newer info"},
        )
        d = conflict_to_dict(c)
        c2 = dict_to_conflict(d)
        assert c2.id == c.id
        assert c2.resolution == c.resolution
        assert c2.metadata == c.metadata
        d2 = conflict_to_dict(c2)
        assert d == d2


class TestConsolidationLogRoundtrip:
    def test_consolidation_log_roundtrip(self):
        entry = ConsolidationLogEntry(
            id="cl1", consolidated_memory_id="m3",
            original_memory_ids=["m1", "m2"],
            strategy="merge", model_used="gpt-4",
            original_count=2, created_at="2026-01-01T00:00:00Z",
            metadata={"run": 1},
        )
        d = consolidation_log_to_dict(entry)
        entry2 = dict_to_consolidation_log(d)
        assert entry2.id == entry.id
        assert entry2.original_memory_ids == entry.original_memory_ids
        assert entry2.metadata == entry.metadata
        d2 = consolidation_log_to_dict(entry2)
        assert d == d2


class TestEmbeddingSerialization:
    def test_embedding_base64_roundtrip(self):
        raw = struct.pack("4f", 0.1, 0.2, 0.3, 0.4)
        b64 = serialize_embedding(raw)
        assert isinstance(b64, str)
        decoded = deserialize_embedding(b64)
        assert decoded == raw

    def test_large_embedding_roundtrip(self):
        raw = struct.pack("384f", *[float(i) / 384 for i in range(384)])
        b64 = serialize_embedding(raw)
        decoded = deserialize_embedding(b64)
        assert decoded == raw


class TestFilenameGeneration:
    def test_memory_to_filename_safe(self):
        m = _make_memory(content="Fix: SQLite 'locked' error @home/user/db")
        name = memory_to_filename(m)
        assert name.endswith(".md")
        assert "'" not in name
        assert "@" not in name
        assert "/" not in name

    def test_memory_to_filename_empty_content(self):
        m = _make_memory(content="")
        name = memory_to_filename(m)
        assert name.startswith(m.id[:12])
        assert name.endswith(".md")

    def test_memory_to_filename_long_truncated(self):
        m = _make_memory(content="a" * 300)
        name = memory_to_filename(m)
        assert len(name) <= 200

    def test_memory_to_filename_unicode(self):
        m = _make_memory(content="日本語テスト with spaces")
        name = memory_to_filename(m)
        assert name.endswith(".md")
        # Should not crash

    def test_slugify_special_chars(self):
        assert slugify("Hello, World! @#$%") == "hello-world"

    def test_slugify_empty(self):
        assert slugify("") == "untitled"

    def test_deterministic_ordering(self):
        m = _make_memory()
        d1 = memory_to_dict(m)
        d2 = memory_to_dict(m)
        assert list(d1.keys()) == list(d2.keys())
        assert d1 == d2
