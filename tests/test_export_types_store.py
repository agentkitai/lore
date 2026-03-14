"""Tests for S3: New types and Store ABC bulk methods."""

from __future__ import annotations

import pytest

from lore.store.memory import MemoryStore
from lore.store.sqlite import SqliteStore
from lore.types import (
    ConflictEntry,
    ConsolidationLogEntry,
    Entity,
    EntityMention,
    ExportFilter,
    ExportResult,
    Fact,
    ImportResult,
    Memory,
)

# ── Type tests ──────────────────────────────────────────────────────

class TestExportResultFields:
    def test_all_fields(self):
        r = ExportResult(
            path="/tmp/export.json", format="json",
            memories=10, entities=5, relationships=3,
            entity_mentions=7, facts=4, conflicts=1,
            consolidation_logs=2, content_hash="sha256:abc",
            duration_ms=123,
        )
        assert r.path == "/tmp/export.json"
        assert r.memories == 10
        assert r.content_hash == "sha256:abc"
        assert r.duration_ms == 123


class TestImportResultDefaults:
    def test_defaults(self):
        r = ImportResult()
        assert r.total == 0
        assert r.imported == 0
        assert r.warnings == []
        assert r.embeddings_regenerated == 0

    def test_warnings_default_is_mutable(self):
        r1 = ImportResult()
        r2 = ImportResult()
        r1.warnings.append("test")
        assert r2.warnings == []


class TestExportFilter:
    def test_all_none(self):
        f = ExportFilter()
        assert f.project is None
        assert f.type is None
        assert f.tier is None
        assert f.since is None

    def test_with_values(self):
        f = ExportFilter(project="lore", type="code", since="2026-01-01")
        assert f.project == "lore"
        assert f.type == "code"


# ── Store bulk method tests ─────────────────────────────────────────

def _make_memory(mid: str = "m1", project: str = None) -> Memory:
    return Memory(
        id=mid, content="test", created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00", project=project,
    )


def _make_fact(fid: str = "f1", memory_id: str = "m1") -> Fact:
    return Fact(
        id=fid, memory_id=memory_id, subject="s", predicate="p",
        object="o", extracted_at="2026-01-01T00:00:00",
    )


def _make_conflict(cid: str = "c1") -> ConflictEntry:
    return ConflictEntry(
        id=cid, new_memory_id="m2", old_fact_id="f1", new_fact_id="f2",
        subject="s", predicate="p", old_value="old", new_value="new",
        resolution="SUPERSEDE", resolved_at="2026-01-01T00:00:00",
    )


def _make_consolidation_log(lid: str = "cl1") -> ConsolidationLogEntry:
    return ConsolidationLogEntry(
        id=lid, consolidated_memory_id="m3",
        original_memory_ids=["m1", "m2"],
        strategy="merge", model_used=None,
        original_count=2, created_at="2026-01-01T00:00:00",
    )


@pytest.fixture
def sqlite_store(tmp_path):
    db_path = str(tmp_path / "test.db")
    return SqliteStore(db_path, knowledge_graph=True)


class TestBaseStoreDefaults:
    def test_defaults_return_empty(self):
        store = MemoryStore()
        assert store.list_all_facts() == []
        assert store.list_all_entity_mentions() == []
        assert store.list_all_conflicts() == []
        assert store.list_all_consolidation_logs() == []


class TestSqliteListAllFacts:
    def test_returns_all_facts(self, sqlite_store):
        sqlite_store.save(_make_memory("m1"))
        sqlite_store.save(_make_memory("m2"))
        sqlite_store.save_fact(_make_fact("f1", "m1"))
        sqlite_store.save_fact(_make_fact("f2", "m2"))
        facts = sqlite_store.list_all_facts()
        assert len(facts) == 2

    def test_filtered_by_memory_ids(self, sqlite_store):
        sqlite_store.save(_make_memory("m1"))
        sqlite_store.save(_make_memory("m2"))
        sqlite_store.save_fact(_make_fact("f1", "m1"))
        sqlite_store.save_fact(_make_fact("f2", "m2"))
        facts = sqlite_store.list_all_facts(memory_ids=["m1"])
        assert len(facts) == 1
        assert facts[0].memory_id == "m1"

    def test_empty_filter_returns_empty(self, sqlite_store):
        sqlite_store.save(_make_memory("m1"))
        sqlite_store.save_fact(_make_fact("f1", "m1"))
        facts = sqlite_store.list_all_facts(memory_ids=[])
        assert facts == []


class TestSqliteListAllEntityMentions:
    def test_returns_all_mentions(self, sqlite_store):
        sqlite_store.save(_make_memory("m1"))
        e = Entity(
            id="e1", name="test", entity_type="tool",
            first_seen_at="2026-01-01", last_seen_at="2026-01-01",
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        sqlite_store.save_entity(e)
        em = EntityMention(
            id="em1", entity_id="e1", memory_id="m1",
            created_at="2026-01-01",
        )
        sqlite_store.save_entity_mention(em)
        mentions = sqlite_store.list_all_entity_mentions()
        assert len(mentions) == 1

    def test_filtered_by_memory_ids(self, sqlite_store):
        sqlite_store.save(_make_memory("m1"))
        sqlite_store.save(_make_memory("m2"))
        e = Entity(
            id="e1", name="test", entity_type="tool",
            first_seen_at="2026-01-01", last_seen_at="2026-01-01",
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        sqlite_store.save_entity(e)
        sqlite_store.save_entity_mention(EntityMention(
            id="em1", entity_id="e1", memory_id="m1", created_at="2026-01-01",
        ))
        sqlite_store.save_entity_mention(EntityMention(
            id="em2", entity_id="e1", memory_id="m2", created_at="2026-01-01",
        ))
        mentions = sqlite_store.list_all_entity_mentions(memory_ids=["m1"])
        assert len(mentions) == 1
        assert mentions[0].memory_id == "m1"


class TestSqliteListAllConflicts:
    def test_returns_all_conflicts(self, sqlite_store):
        sqlite_store.save(_make_memory("m2"))
        sqlite_store.save_conflict(_make_conflict("c1"))
        sqlite_store.save_conflict(_make_conflict("c2"))
        conflicts = sqlite_store.list_all_conflicts()
        assert len(conflicts) == 2


class TestSqliteListAllConsolidationLogs:
    def test_returns_all_logs(self, sqlite_store):
        sqlite_store.save_consolidation_log(_make_consolidation_log("cl1"))
        sqlite_store.save_consolidation_log(_make_consolidation_log("cl2"))
        logs = sqlite_store.list_all_consolidation_logs()
        assert len(logs) == 2
