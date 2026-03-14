"""Tests for S4: JSON Export Engine and S5: Lore.export_data() + CLI."""

from __future__ import annotations

import json
import os

import pytest

from lore.export.exporter import Exporter
from lore.export.schema import verify_content_hash
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


@pytest.fixture
def store(tmp_path):
    str(tmp_path / "test.db")
    return MemoryStore()


def _seed_data(store):
    """Populate store with representative test data."""
    m1 = Memory(
        id="m1", content="SQLite WAL mode fixes concurrency",
        type="code", tier="long", project="lore",
        tags=["sqlite"], source="test",
        created_at="2026-01-10T10:00:00Z",
        updated_at="2026-01-10T10:00:00Z",
        confidence=0.9, importance_score=0.8,
    )
    m2 = Memory(
        id="m2", content="Docker build fails on M1",
        type="lesson", tier="short", project="other",
        tags=["docker"], source="test",
        created_at="2026-01-15T10:00:00Z",
        updated_at="2026-01-15T10:00:00Z",
        archived=True,
    )
    store.save(m1)
    store.save(m2)

    e1 = Entity(
        id="e1", name="SQLite", entity_type="tool",
        aliases=["sqlite3"],
        first_seen_at="2026-01-10", last_seen_at="2026-01-15",
        created_at="2026-01-10", updated_at="2026-01-15",
    )
    e2 = Entity(
        id="e2", name="Docker", entity_type="tool",
        first_seen_at="2026-01-15", last_seen_at="2026-01-15",
        created_at="2026-01-15", updated_at="2026-01-15",
    )
    store.save_entity(e1)
    store.save_entity(e2)

    r1 = Relationship(
        id="r1", source_entity_id="e1", target_entity_id="e2",
        rel_type="related_to", weight=0.5,
        valid_from="2026-01-10",
        created_at="2026-01-10", updated_at="2026-01-10",
    )
    store.save_relationship(r1)

    em1 = EntityMention(
        id="em1", entity_id="e1", memory_id="m1",
        created_at="2026-01-10",
    )
    em2 = EntityMention(
        id="em2", entity_id="e2", memory_id="m2",
        created_at="2026-01-15",
    )
    store.save_entity_mention(em1)
    store.save_entity_mention(em2)

    f1 = Fact(
        id="f1", memory_id="m1",
        subject="sqlite", predicate="uses", object="WAL mode",
        extracted_at="2026-01-10T10:00:00Z",
    )
    store.save_fact(f1)

    c1 = ConflictEntry(
        id="c1", new_memory_id="m1", old_fact_id="f0", new_fact_id="f1",
        subject="sqlite", predicate="mode",
        old_value="journal", new_value="WAL",
        resolution="SUPERSEDE", resolved_at="2026-01-10T10:00:00Z",
    )
    store.save_conflict(c1)

    cl1 = ConsolidationLogEntry(
        id="cl1", consolidated_memory_id="m1",
        original_memory_ids=["m0", "m1"],
        strategy="merge", model_used=None,
        original_count=2, created_at="2026-01-10T10:00:00Z",
    )
    store.save_consolidation_log(cl1)


class TestJsonExportFull:
    def test_export_full_json(self, store, tmp_path):
        _seed_data(store)
        exporter = Exporter(store)
        output = str(tmp_path / "export.json")
        result = exporter.export(output=output)

        assert os.path.exists(output)
        assert result.memories == 2
        assert result.entities == 2
        assert result.relationships == 1
        assert result.facts == 1
        assert result.conflicts == 1
        assert result.consolidation_logs == 1
        assert result.content_hash.startswith("sha256:")

        with open(output) as f:
            data = json.load(f)
        assert data["schema_version"] == 1
        assert "exported_at" in data
        assert "lore_version" in data
        assert len(data["data"]["memories"]) == 2
        verify_content_hash(data)

    def test_export_empty_database(self, store, tmp_path):
        exporter = Exporter(store)
        output = str(tmp_path / "empty.json")
        result = exporter.export(output=output)
        assert result.memories == 0
        with open(output) as f:
            data = json.load(f)
        assert data["counts"]["memories"] == 0

    def test_export_archived_memories_included(self, store, tmp_path):
        _seed_data(store)
        exporter = Exporter(store)
        output = str(tmp_path / "export.json")
        result = exporter.export(output=output)
        assert result.memories == 2  # includes archived m2

    def test_export_deterministic_ordering(self, store, tmp_path):
        _seed_data(store)
        exporter = Exporter(store)
        out1 = str(tmp_path / "out1.json")
        out2 = str(tmp_path / "out2.json")
        r1 = exporter.export(output=out1)
        r2 = exporter.export(output=out2)
        assert r1.content_hash == r2.content_hash

        with open(out1) as f:
            d1 = json.load(f)
        with open(out2) as f:
            d2 = json.load(f)
        # Data sections should be identical
        assert d1["data"] == d2["data"]


class TestJsonExportFiltered:
    def test_export_filtered_by_project(self, store, tmp_path):
        _seed_data(store)
        exporter = Exporter(store)
        output = str(tmp_path / "filtered.json")
        filters = ExportFilter(project="lore")
        result = exporter.export(output=output, filters=filters)
        assert result.memories == 1
        with open(output) as f:
            data = json.load(f)
        assert data["data"]["memories"][0]["project"] == "lore"
        assert data["filters"]["project"] == "lore"

    def test_export_filtered_by_type(self, store, tmp_path):
        _seed_data(store)
        exporter = Exporter(store)
        output = str(tmp_path / "filtered.json")
        filters = ExportFilter(type="code")
        result = exporter.export(output=output, filters=filters)
        assert result.memories == 1

    def test_export_filtered_by_tier(self, store, tmp_path):
        _seed_data(store)
        exporter = Exporter(store)
        output = str(tmp_path / "filtered.json")
        filters = ExportFilter(tier="long")
        result = exporter.export(output=output, filters=filters)
        assert result.memories == 1

    def test_export_filtered_by_since(self, store, tmp_path):
        _seed_data(store)
        exporter = Exporter(store)
        output = str(tmp_path / "filtered.json")
        filters = ExportFilter(since="2026-01-12T00:00:00Z")
        result = exporter.export(output=output, filters=filters)
        assert result.memories == 1

    def test_export_graph_scoping(self, store, tmp_path):
        _seed_data(store)
        exporter = Exporter(store)
        output = str(tmp_path / "scoped.json")
        filters = ExportFilter(project="lore")
        result = exporter.export(output=output, filters=filters)
        # Only SQLite entity should be included (linked to m1 via em1)
        assert result.entities == 1
        with open(output) as f:
            data = json.load(f)
        assert data["data"]["entities"][0]["name"] == "SQLite"
        # Relationship r1 requires both e1 and e2, but e2 is scoped out
        assert result.relationships == 0


class TestJsonExportOptions:
    def test_export_embeddings_excluded_default(self, store, tmp_path):
        m = Memory(
            id="m_emb", content="test", embedding=b"\x00\x01\x02",
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        store.save(m)
        exporter = Exporter(store)
        output = str(tmp_path / "no_emb.json")
        exporter.export(output=output)
        with open(output) as f:
            data = json.load(f)
        assert data["data"]["memories"][0]["embedding"] is None

    def test_export_embeddings_included(self, store, tmp_path):
        m = Memory(
            id="m_emb", content="test", embedding=b"\x00\x01\x02",
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        store.save(m)
        exporter = Exporter(store)
        output = str(tmp_path / "with_emb.json")
        exporter.export(output=output, include_embeddings=True)
        with open(output) as f:
            data = json.load(f)
        assert data["data"]["memories"][0]["embedding"] is not None

    def test_export_pretty_print(self, store, tmp_path):
        store.save(Memory(
            id="m1", content="test",
            created_at="2026-01-01", updated_at="2026-01-01",
        ))
        exporter = Exporter(store)
        output = str(tmp_path / "pretty.json")
        exporter.export(output=output, pretty=True)
        with open(output) as f:
            text = f.read()
        assert "\n" in text  # Pretty printed
        assert "  " in text

    def test_export_default_filename(self, store, tmp_path):
        store.save(Memory(
            id="m1", content="test",
            created_at="2026-01-01", updated_at="2026-01-01",
        ))
        exporter = Exporter(store)
        # Change to tmp_path so default file goes there
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = exporter.export()
            assert "lore-export-" in result.path
            assert result.path.endswith(".json")
        finally:
            os.chdir(old_cwd)

    def test_export_custom_output_path(self, store, tmp_path):
        store.save(Memory(
            id="m1", content="test",
            created_at="2026-01-01", updated_at="2026-01-01",
        ))
        exporter = Exporter(store)
        output = str(tmp_path / "custom" / "path.json")
        result = exporter.export(output=output)
        assert result.path == output
        assert os.path.exists(output)

    def test_export_envelope_metadata(self, store, tmp_path):
        store.save(Memory(
            id="m1", content="test",
            created_at="2026-01-01", updated_at="2026-01-01",
        ))
        exporter = Exporter(store)
        output = str(tmp_path / "meta.json")
        exporter.export(output=output)
        with open(output) as f:
            data = json.load(f)
        assert "schema_version" in data
        assert "exported_at" in data
        assert "lore_version" in data
        assert "content_hash" in data
        assert "counts" in data


class TestLoreExportData:
    def test_lore_export_data_json(self, tmp_path):
        from lore import Lore
        str(tmp_path / "lore.db")
        lore = Lore(store=MemoryStore())
        lore.remember("test memory", type="code")
        output = str(tmp_path / "export.json")
        result = lore.export_data(format="json", output=output)
        lore.close()
        assert result.memories == 1
        assert os.path.exists(output)

    def test_lore_export_data_with_filters(self, tmp_path):
        from lore import Lore
        str(tmp_path / "lore.db")
        lore = Lore(store=MemoryStore())
        lore.remember("test code", type="code", project="proj1")
        lore.remember("test lesson", type="lesson", project="proj2")
        output = str(tmp_path / "filtered.json")
        result = lore.export_data(format="json", output=output, project="proj1")
        lore.close()
        assert result.memories == 1
