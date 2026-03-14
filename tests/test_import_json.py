"""Tests for S6: JSON Import Engine and S7: Lore.import_data() + CLI."""

from __future__ import annotations

import json

import pytest

from lore.export.exporter import Exporter
from lore.export.importer import Importer
from lore.export.schema import compute_content_hash
from lore.store.sqlite import SqliteStore
from lore.types import (
    Entity,
    EntityMention,
    Fact,
    Memory,
)


@pytest.fixture
def store(tmp_path):
    db = str(tmp_path / "test.db")
    return SqliteStore(db, knowledge_graph=True)


def _seed_and_export(store, tmp_path):
    """Create data, export it, return the export file path."""
    m1 = Memory(
        id="m1", content="Test memory one", type="code", tier="long",
        project="proj", tags=["tag1"], source="test",
        created_at="2026-01-10T10:00:00Z", updated_at="2026-01-10T10:00:00Z",
    )
    m2 = Memory(
        id="m2", content="Test memory two", type="lesson", tier="short",
        project="proj", tags=["tag2"], source="test",
        created_at="2026-01-15T10:00:00Z", updated_at="2026-01-15T10:00:00Z",
    )
    store.save(m1)
    store.save(m2)

    e1 = Entity(
        id="e1", name="TestEntity", entity_type="concept",
        first_seen_at="2026-01-10", last_seen_at="2026-01-15",
        created_at="2026-01-10", updated_at="2026-01-15",
    )
    store.save_entity(e1)

    em1 = EntityMention(
        id="em1", entity_id="e1", memory_id="m1",
        created_at="2026-01-10",
    )
    store.save_entity_mention(em1)

    f1 = Fact(
        id="f1", memory_id="m1",
        subject="test", predicate="is", object="true",
        extracted_at="2026-01-10T10:00:00Z",
    )
    store.save_fact(f1)

    output = str(tmp_path / "export.json")
    exporter = Exporter(store)
    exporter.export(output=output)
    return output


class TestImportFull:
    def test_import_full_json(self, tmp_path):
        # Create source store and export
        src_store = SqliteStore(str(tmp_path / "src.db"), knowledge_graph=True)
        export_path = _seed_and_export(src_store, tmp_path)

        # Import into fresh store
        dst_store = SqliteStore(str(tmp_path / "dst.db"), knowledge_graph=True)
        importer = Importer(dst_store)
        result = importer.import_file(export_path)

        assert result.imported == 2
        assert result.errors == 0
        assert dst_store.get("m1") is not None
        assert dst_store.get("m2") is not None

    def test_import_idempotent(self, tmp_path):
        src_store = SqliteStore(str(tmp_path / "src.db"), knowledge_graph=True)
        export_path = _seed_and_export(src_store, tmp_path)

        dst_store = SqliteStore(str(tmp_path / "dst.db"), knowledge_graph=True)
        importer = Importer(dst_store)
        r1 = importer.import_file(export_path)
        r2 = importer.import_file(export_path)

        assert r1.imported == 2
        assert r2.imported == 0
        assert r2.skipped == 2

    def test_import_overwrite(self, tmp_path):
        src_store = SqliteStore(str(tmp_path / "src.db"), knowledge_graph=True)
        export_path = _seed_and_export(src_store, tmp_path)

        dst_store = SqliteStore(str(tmp_path / "dst.db"), knowledge_graph=True)
        importer = Importer(dst_store)
        importer.import_file(export_path)

        r2 = importer.import_file(export_path, overwrite=True)
        assert r2.overwritten == 2
        assert r2.skipped == 0

    def test_import_dry_run(self, tmp_path):
        src_store = SqliteStore(str(tmp_path / "src.db"), knowledge_graph=True)
        export_path = _seed_and_export(src_store, tmp_path)

        dst_store = SqliteStore(str(tmp_path / "dst.db"), knowledge_graph=True)
        importer = Importer(dst_store)
        result = importer.import_file(export_path, dry_run=True)

        assert result.imported == 2
        assert dst_store.get("m1") is None  # Nothing actually written

    def test_import_project_override(self, tmp_path):
        src_store = SqliteStore(str(tmp_path / "src.db"), knowledge_graph=True)
        export_path = _seed_and_export(src_store, tmp_path)

        dst_store = SqliteStore(str(tmp_path / "dst.db"), knowledge_graph=True)
        importer = Importer(dst_store)
        importer.import_file(export_path, project_override="override-proj")

        m = dst_store.get("m1")
        assert m.project == "override-proj"

    def test_import_empty_database(self, tmp_path):
        # Export from empty store
        empty_store = SqliteStore(str(tmp_path / "empty.db"), knowledge_graph=True)
        output = str(tmp_path / "empty_export.json")
        Exporter(empty_store).export(output=output)

        dst_store = SqliteStore(str(tmp_path / "dst.db"), knowledge_graph=True)
        result = Importer(dst_store).import_file(output)
        assert result.total == 0
        assert result.imported == 0


class TestImportValidation:
    def test_import_schema_version_mismatch(self, tmp_path):
        data = {
            "schema_version": 999,
            "exported_at": "2026-01-01",
            "lore_version": "0.9.5",
            "data": {"memories": []},
        }
        path = str(tmp_path / "future.json")
        with open(path, "w") as f:
            json.dump(data, f)

        store = SqliteStore(str(tmp_path / "dst.db"))
        importer = Importer(store)
        with pytest.raises(ValueError, match="newer"):
            importer.import_file(path)

    def test_import_content_hash_mismatch(self, tmp_path):
        data = {"memories": [{"id": "m1", "content": "test"}]}
        envelope = {
            "schema_version": 1,
            "exported_at": "2026-01-01",
            "lore_version": "0.9.5",
            "content_hash": "sha256:wrong",
            "data": data,
        }
        path = str(tmp_path / "tampered.json")
        with open(path, "w") as f:
            json.dump(envelope, f)

        store = SqliteStore(str(tmp_path / "dst.db"))
        importer = Importer(store)
        with pytest.raises(ValueError, match="mismatch"):
            importer.import_file(path)

    def test_import_corrupted_json(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("{not json")

        store = SqliteStore(str(tmp_path / "dst.db"))
        importer = Importer(store)
        with pytest.raises(ValueError, match="Invalid JSON"):
            importer.import_file(path)

    def test_import_missing_required_fields(self, tmp_path):
        data = {"memories": [{"id": "m1"}]}  # missing content
        content_hash = compute_content_hash(data)
        envelope = {
            "schema_version": 1,
            "exported_at": "2026-01-01",
            "content_hash": content_hash,
            "data": data,
        }
        path = str(tmp_path / "incomplete.json")
        with open(path, "w") as f:
            json.dump(envelope, f)

        store = SqliteStore(str(tmp_path / "dst.db"))
        result = Importer(store).import_file(path)
        assert result.errors == 1
        assert len(result.warnings) == 1

    def test_import_extra_unknown_fields(self, tmp_path):
        data = {"memories": [
            {"id": "m1", "content": "test", "unknown_field": "ignored",
             "created_at": "2026-01-01", "updated_at": "2026-01-01"}
        ]}
        content_hash = compute_content_hash(data)
        envelope = {
            "schema_version": 1,
            "exported_at": "2026-01-01",
            "content_hash": content_hash,
            "data": data,
        }
        path = str(tmp_path / "extra.json")
        with open(path, "w") as f:
            json.dump(envelope, f)

        store = SqliteStore(str(tmp_path / "dst.db"))
        result = Importer(store).import_file(path)
        assert result.imported == 1
        assert result.errors == 0


class TestImportGraphIntegrity:
    def test_import_orphaned_relationship_warning(self, tmp_path):
        data = {
            "memories": [],
            "entities": [
                {"id": "e1", "name": "A", "entity_type": "concept",
                 "created_at": "2026-01-01", "updated_at": "2026-01-01",
                 "first_seen_at": "2026-01-01", "last_seen_at": "2026-01-01"},
            ],
            "relationships": [
                {"id": "r1", "source_entity_id": "e1", "target_entity_id": "e_missing",
                 "rel_type": "uses", "valid_from": "2026-01-01",
                 "created_at": "2026-01-01", "updated_at": "2026-01-01"},
            ],
        }
        content_hash = compute_content_hash(data)
        envelope = {
            "schema_version": 1, "exported_at": "2026-01-01",
            "content_hash": content_hash, "data": data,
        }
        path = str(tmp_path / "orphan.json")
        with open(path, "w") as f:
            json.dump(envelope, f)

        store = SqliteStore(str(tmp_path / "dst.db"), knowledge_graph=True)
        result = Importer(store).import_file(path)
        assert any("Orphaned relationship" in w for w in result.warnings)

    def test_import_orphaned_mention_warning(self, tmp_path):
        data = {
            "memories": [
                {"id": "m1", "content": "test", "created_at": "2026-01-01",
                 "updated_at": "2026-01-01"},
            ],
            "entities": [],
            "entity_mentions": [
                {"id": "em1", "entity_id": "e_missing", "memory_id": "m1",
                 "created_at": "2026-01-01"},
            ],
        }
        content_hash = compute_content_hash(data)
        envelope = {
            "schema_version": 1, "exported_at": "2026-01-01",
            "content_hash": content_hash, "data": data,
        }
        path = str(tmp_path / "orphan_mention.json")
        with open(path, "w") as f:
            json.dump(envelope, f)

        store = SqliteStore(str(tmp_path / "dst.db"), knowledge_graph=True)
        result = Importer(store).import_file(path)
        assert any("Orphaned mention" in w for w in result.warnings)


class TestLoreImportData:
    def test_lore_import_data(self, tmp_path):
        from lore import Lore
        db1 = str(tmp_path / "src.db")
        lore1 = Lore(db_path=db1)
        lore1.remember("import test memory")
        output = str(tmp_path / "export.json")
        lore1.export_data(output=output)
        lore1.close()

        db2 = str(tmp_path / "dst.db")
        lore2 = Lore(db_path=db2)
        result = lore2.import_data(output, skip_embeddings=True)
        lore2.close()
        assert result.imported == 1

    def test_lore_import_data_dry_run(self, tmp_path):
        from lore import Lore
        db1 = str(tmp_path / "src.db")
        lore1 = Lore(db_path=db1)
        lore1.remember("dry run test")
        output = str(tmp_path / "export.json")
        lore1.export_data(output=output)
        lore1.close()

        db2 = str(tmp_path / "dst.db")
        lore2 = Lore(db_path=db2)
        result = lore2.import_data(output, dry_run=True, skip_embeddings=True)
        assert result.imported == 1
        assert lore2._store.count() == 0  # nothing written
        lore2.close()
