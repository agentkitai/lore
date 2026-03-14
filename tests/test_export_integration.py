"""S13: Round-trip integration tests and edge cases for E5 Export/Snapshot."""

from __future__ import annotations

import json
import struct

from lore.export.exporter import Exporter
from lore.export.importer import Importer
from lore.store.memory import MemoryStore
from lore.types import (
    ConflictEntry,
    ConsolidationLogEntry,
    Entity,
    EntityMention,
    ExportFilter,
    Fact,
    Memory,
    Relationship,
)


def _make_full_dataset(store):
    """Create a comprehensive dataset with all data types."""
    m1 = Memory(
        id="m1", content="SQLite WAL mode fixes concurrency",
        type="code", tier="long", project="lore",
        context="debugging session", tags=["sqlite", "concurrency"],
        metadata={"key": "value"}, source="claude-code",
        embedding=struct.pack("4f", 0.1, 0.2, 0.3, 0.4),
        created_at="2026-01-10T10:00:00Z",
        updated_at="2026-01-10T10:30:00Z",
        ttl=None, expires_at=None,
        confidence=0.95, upvotes=3, downvotes=1,
        importance_score=0.82, access_count=5,
        last_accessed_at="2026-01-10T10:30:00Z",
        archived=False, consolidated_into=None,
    )
    m2 = Memory(
        id="m2", content="Docker M1 build fix: use --platform linux/amd64",
        type="lesson", tier="short", project="other",
        tags=["docker", "m1"], source="test",
        created_at="2026-01-15T10:00:00Z",
        updated_at="2026-01-15T10:00:00Z",
        archived=True,
    )
    m3 = Memory(
        id="m3", content="Expired but not cleaned up yet",
        type="general", tier="working",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        expires_at="2026-01-02T00:00:00Z",
    )
    store.save(m1)
    store.save(m2)
    store.save(m3)

    e1 = Entity(
        id="e1", name="SQLite", entity_type="tool",
        aliases=["sqlite3", "SQLite3"],
        description="An embedded database",
        metadata={"wiki": "link"},
        mention_count=5,
        first_seen_at="2026-01-01T00:00:00Z",
        last_seen_at="2026-01-15T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-15T00:00:00Z",
    )
    e2 = Entity(
        id="e2", name="Docker", entity_type="tool",
        first_seen_at="2026-01-15T00:00:00Z",
        last_seen_at="2026-01-15T00:00:00Z",
        created_at="2026-01-15T00:00:00Z",
        updated_at="2026-01-15T00:00:00Z",
    )
    store.save_entity(e1)
    store.save_entity(e2)

    r1 = Relationship(
        id="r1", source_entity_id="e1", target_entity_id="e2",
        rel_type="related_to", weight=0.8,
        properties={"context": "containerized apps"},
        source_fact_id="f1", source_memory_id="m1",
        valid_from="2026-01-10T00:00:00Z", valid_until=None,
        created_at="2026-01-10T00:00:00Z",
        updated_at="2026-01-10T00:00:00Z",
    )
    store.save_relationship(r1)

    em1 = EntityMention(
        id="em1", entity_id="e1", memory_id="m1",
        mention_type="explicit", confidence=0.9,
        created_at="2026-01-10T00:00:00Z",
    )
    em2 = EntityMention(
        id="em2", entity_id="e2", memory_id="m2",
        created_at="2026-01-15T00:00:00Z",
    )
    store.save_entity_mention(em1)
    store.save_entity_mention(em2)

    f1 = Fact(
        id="f1", memory_id="m1",
        subject="sqlite", predicate="uses", object="WAL mode",
        confidence=0.95, extracted_at="2026-01-10T10:00:00Z",
        metadata={"source": "auto"},
    )
    store.save_fact(f1)

    c1 = ConflictEntry(
        id="c1", new_memory_id="m1", old_fact_id="f0", new_fact_id="f1",
        subject="sqlite", predicate="mode",
        old_value="journal", new_value="WAL",
        resolution="SUPERSEDE", resolved_at="2026-01-10T10:00:00Z",
        metadata={"reasoning": "newer info"},
    )
    store.save_conflict(c1)

    cl1 = ConsolidationLogEntry(
        id="cl1", consolidated_memory_id="m1",
        original_memory_ids=["m0", "m1"],
        strategy="merge", model_used="gpt-4",
        original_count=2, created_at="2026-01-10T10:00:00Z",
        metadata={"run": 1},
    )
    store.save_consolidation_log(cl1)


class TestFullRoundTrip:
    """Export → wipe → import → export → compare."""

    def test_full_roundtrip(self, tmp_path):
        # 1. Create source data
        src_store = MemoryStore()
        _make_full_dataset(src_store)

        # 2. Export (no embeddings)
        export1 = str(tmp_path / "export1.json")
        r1 = Exporter(src_store).export(output=export1)
        assert r1.memories == 3
        assert r1.entities == 2
        assert r1.relationships == 1
        assert r1.facts == 1
        assert r1.conflicts == 1
        assert r1.consolidation_logs == 1

        # 3. Import into fresh DB
        dst_store = MemoryStore()
        importer = Importer(dst_store)
        ir = importer.import_file(export1)
        assert ir.imported == 3
        assert ir.errors == 0

        # 4. Re-export from destination
        export2 = str(tmp_path / "export2.json")
        r2 = Exporter(dst_store).export(output=export2)

        # 5. Verify data sections are identical
        with open(export1) as f:
            d1 = json.load(f)
        with open(export2) as f:
            d2 = json.load(f)

        assert d1["data"] == d2["data"]
        assert r1.content_hash == r2.content_hash

    def test_roundtrip_with_embeddings(self, tmp_path):
        src_store = MemoryStore()
        _make_full_dataset(src_store)

        export1 = str(tmp_path / "export1.json")
        Exporter(src_store).export(output=export1, include_embeddings=True)

        dst_store = MemoryStore()
        Importer(dst_store).import_file(export1)

        export2 = str(tmp_path / "export2.json")
        Exporter(dst_store).export(output=export2, include_embeddings=True)

        with open(export1) as f:
            d1 = json.load(f)
        with open(export2) as f:
            d2 = json.load(f)

        assert d1["data"] == d2["data"]

    def test_roundtrip_graph_integrity(self, tmp_path):
        src_store = MemoryStore()
        _make_full_dataset(src_store)

        export_path = str(tmp_path / "export.json")
        Exporter(src_store).export(output=export_path)

        dst_store = MemoryStore()
        Importer(dst_store).import_file(export_path)

        # Verify entities
        entities = dst_store.list_entities()
        assert len(entities) == 2
        assert {e.name for e in entities} == {"SQLite", "Docker"}

        # Verify relationships
        rels = dst_store.list_relationships(include_expired=True, limit=100)
        assert len(rels) == 1
        assert rels[0].rel_type == "related_to"

        # Verify mentions
        mentions = dst_store.list_all_entity_mentions()
        assert len(mentions) == 2

    def test_roundtrip_facts_and_conflicts(self, tmp_path):
        src_store = MemoryStore()
        _make_full_dataset(src_store)

        export_path = str(tmp_path / "export.json")
        Exporter(src_store).export(output=export_path)

        dst_store = MemoryStore()
        Importer(dst_store).import_file(export_path)

        facts = dst_store.list_all_facts()
        assert len(facts) == 1
        assert facts[0].subject == "sqlite"

        conflicts = dst_store.list_all_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0].resolution == "SUPERSEDE"

        logs = dst_store.list_all_consolidation_logs()
        assert len(logs) == 1
        assert logs[0].strategy == "merge"

    def test_roundtrip_with_project_override(self, tmp_path):
        src_store = MemoryStore()
        _make_full_dataset(src_store)

        export_path = str(tmp_path / "export.json")
        Exporter(src_store).export(output=export_path)

        dst_store = MemoryStore()
        Importer(dst_store).import_file(export_path, project_override="new-project")

        for m in dst_store.list(include_archived=True):
            assert m.project == "new-project"

    def test_roundtrip_filtered_export(self, tmp_path):
        src_store = MemoryStore()
        _make_full_dataset(src_store)

        export_path = str(tmp_path / "filtered.json")
        filters = ExportFilter(project="lore")
        r = Exporter(src_store).export(output=export_path, filters=filters)
        assert r.memories == 1

        dst_store = MemoryStore()
        ir = Importer(dst_store).import_file(export_path)
        assert ir.imported == 1
        assert dst_store.get("m1") is not None
        assert dst_store.get("m2") is None


class TestEdgeCases:
    def test_export_empty_database(self, tmp_path):
        store = MemoryStore()
        output = str(tmp_path / "empty.json")
        r = Exporter(store).export(output=output)
        assert r.memories == 0
        with open(output) as f:
            data = json.load(f)
        assert data["counts"]["memories"] == 0

    def test_export_unicode_and_emoji(self, tmp_path):
        store = MemoryStore()
        store.save(Memory(
            id="m-emoji", content="🎉 日本語 عربي Ñoño",
            type="general",
            created_at="2026-01-01", updated_at="2026-01-01",
        ))

        output = str(tmp_path / "unicode.json")
        Exporter(store).export(output=output)

        dst = MemoryStore()
        Importer(dst).import_file(output)
        m = dst.get("m-emoji")
        assert m.content == "🎉 日本語 عربي Ñoño"

    def test_export_very_long_content(self, tmp_path):
        store = MemoryStore()
        long_content = "x" * 100_000
        store.save(Memory(
            id="m-long", content=long_content,
            created_at="2026-01-01", updated_at="2026-01-01",
        ))

        output = str(tmp_path / "long.json")
        Exporter(store).export(output=output)

        dst = MemoryStore()
        Importer(dst).import_file(output)
        m = dst.get("m-long")
        assert len(m.content) == 100_000

    def test_export_null_everywhere(self, tmp_path):
        store = MemoryStore()
        store.save(Memory(
            id="m-null", content="minimal",
            context=None, metadata=None, source=None, project=None,
            embedding=None, ttl=None, expires_at=None,
            last_accessed_at=None, consolidated_into=None,
            created_at="2026-01-01", updated_at="2026-01-01",
        ))

        output = str(tmp_path / "nulls.json")
        Exporter(store).export(output=output)

        dst = MemoryStore()
        Importer(dst).import_file(output)
        m = dst.get("m-null")
        assert m.context is None
        assert m.metadata is None
        assert m.source is None

    def test_export_archived_memories(self, tmp_path):
        store = MemoryStore()
        store.save(Memory(
            id="m-archived", content="archived", archived=True,
            created_at="2026-01-01", updated_at="2026-01-01",
        ))

        output = str(tmp_path / "archived.json")
        r = Exporter(store).export(output=output)
        assert r.memories == 1

    def test_export_expired_memories(self, tmp_path):
        store = MemoryStore()
        store.save(Memory(
            id="m-expired", content="expired",
            expires_at="2020-01-01T00:00:00Z",
            created_at="2026-01-01", updated_at="2026-01-01",
        ))

        output = str(tmp_path / "expired.json")
        r = Exporter(store).export(output=output)
        assert r.memories == 1

    def test_import_extra_unknown_fields_forward_compat(self, tmp_path):
        from lore.export.schema import compute_content_hash

        data = {"memories": [
            {"id": "m1", "content": "test", "future_field": "ignored",
             "created_at": "2026-01-01", "updated_at": "2026-01-01"},
        ]}
        content_hash = compute_content_hash(data)
        envelope = {
            "schema_version": 1, "exported_at": "2026-01-01",
            "content_hash": content_hash, "data": data,
        }
        path = str(tmp_path / "forward.json")
        with open(path, "w") as f:
            json.dump(envelope, f)

        store = MemoryStore()
        result = Importer(store).import_file(path)
        assert result.imported == 1
        assert result.errors == 0


class TestMCPTools:
    """Basic smoke tests for MCP tool functions."""

    def test_mcp_export_tool(self, tmp_path, monkeypatch):
        from lore import Lore

        db = str(tmp_path / "lore.db")
        lore = Lore(store=MemoryStore())
        lore.remember("mcp export test")

        import lore.mcp.server as mcp_mod
        monkeypatch.setattr(mcp_mod, "_lore", lore)

        output = str(tmp_path / "mcp_export.json")
        result = mcp_mod.export(output=output)
        assert "Export complete" in result
        assert "Memories: 1" in result
        lore.close()

    def test_mcp_snapshot_tool(self, tmp_path, monkeypatch):
        from lore import Lore

        db = str(tmp_path / "lore.db")
        lore = Lore(store=MemoryStore())
        lore.remember("snapshot test")

        import lore.mcp.server as mcp_mod
        monkeypatch.setattr(mcp_mod, "_lore", lore)
        monkeypatch.setenv("HOME", str(tmp_path))

        result = mcp_mod.snapshot()
        assert "Snapshot created" in result
        lore.close()

    def test_mcp_snapshot_list_tool_empty(self, tmp_path, monkeypatch):
        from lore import Lore
        from lore.export import snapshot as snap_mod

        db = str(tmp_path / "lore.db")
        lore = Lore(store=MemoryStore())

        import lore.mcp.server as mcp_mod
        monkeypatch.setattr(mcp_mod, "_lore", lore)
        # Use a guaranteed-empty snapshots dir
        empty_snap_dir = str(tmp_path / "empty_snapshots")
        monkeypatch.setattr(snap_mod, "_DEFAULT_SNAPSHOTS_DIR", empty_snap_dir)

        result = mcp_mod.snapshot_list()
        assert "No snapshots available" in result
        lore.close()
