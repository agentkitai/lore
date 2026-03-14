"""Tests for S8: Markdown/Obsidian Export Renderer."""

from __future__ import annotations

import os

import pytest

from lore.export.markdown import MarkdownRenderer
from lore.store.memory import MemoryStore
from lore.types import (
    Entity,
    EntityMention,
    ExportFilter,
    Fact,
    Memory,
    Relationship,
)


@pytest.fixture
def store(tmp_path):
    db = str(tmp_path / "test.db")
    return MemoryStore()


def _seed_data(store):
    m1 = Memory(
        id="m1", content="SQLite WAL mode fixes concurrency",
        type="code", tier="long", project="lore",
        tags=["sqlite"], source="test", confidence=0.9,
        importance_score=0.8,
        created_at="2026-01-10T10:00:00Z",
        updated_at="2026-01-10T10:00:00Z",
    )
    m2 = Memory(
        id="m2", content="Docker build tips",
        type="lesson", tier="short", project="other",
        created_at="2026-01-15T10:00:00Z",
        updated_at="2026-01-15T10:00:00Z",
    )
    store.save(m1)
    store.save(m2)

    e1 = Entity(
        id="e1", name="SQLite", entity_type="tool",
        aliases=["sqlite3"],
        first_seen_at="2026-01-10", last_seen_at="2026-01-15",
        created_at="2026-01-10", updated_at="2026-01-15",
    )
    store.save_entity(e1)

    r1 = Relationship(
        id="r1", source_entity_id="e1", target_entity_id="e1",
        rel_type="related_to", weight=0.5,
        valid_from="2026-01-10",
        created_at="2026-01-10", updated_at="2026-01-10",
    )
    store.save_relationship(r1)

    em1 = EntityMention(
        id="em1", entity_id="e1", memory_id="m1",
        created_at="2026-01-10",
    )
    store.save_entity_mention(em1)

    f1 = Fact(
        id="f1", memory_id="m1",
        subject="sqlite", predicate="uses", object="WAL mode",
        extracted_at="2026-01-10T10:00:00Z",
    )
    store.save_fact(f1)


class TestMarkdownDirectoryStructure:
    def test_directory_structure(self, store, tmp_path):
        _seed_data(store)
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-export")
        result = renderer.render(output_dir=out)

        assert os.path.isdir(os.path.join(out, "memories", "code"))
        assert os.path.isdir(os.path.join(out, "memories", "lesson"))
        assert os.path.isdir(os.path.join(out, "entities"))
        assert os.path.isdir(os.path.join(out, "graph"))
        assert os.path.isfile(os.path.join(out, "_export_meta.md"))
        assert os.path.isfile(os.path.join(out, "graph", "relationships.md"))
        assert result.memories == 2
        assert result.format == "markdown"


class TestMarkdownMemoryFiles:
    def test_frontmatter(self, store, tmp_path):
        _seed_data(store)
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-export")
        renderer.render(output_dir=out)

        # Find the code memory file
        code_dir = os.path.join(out, "memories", "code")
        files = os.listdir(code_dir)
        assert len(files) == 1
        content = open(os.path.join(code_dir, files[0])).read()
        assert "---" in content
        assert "id: m1" in content
        assert "type: code" in content
        assert "tier: long" in content

    def test_content_body(self, store, tmp_path):
        _seed_data(store)
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-export")
        renderer.render(output_dir=out)

        code_dir = os.path.join(out, "memories", "code")
        files = os.listdir(code_dir)
        content = open(os.path.join(code_dir, files[0])).read()
        assert "SQLite WAL mode fixes concurrency" in content

    def test_facts_section(self, store, tmp_path):
        _seed_data(store)
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-export")
        renderer.render(output_dir=out)

        code_dir = os.path.join(out, "memories", "code")
        files = os.listdir(code_dir)
        content = open(os.path.join(code_dir, files[0])).read()
        assert "## Facts" in content
        assert "| sqlite | uses | WAL mode |" in content

    def test_wikilinks(self, store, tmp_path):
        _seed_data(store)
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-export")
        renderer.render(output_dir=out)

        code_dir = os.path.join(out, "memories", "code")
        files = os.listdir(code_dir)
        content = open(os.path.join(code_dir, files[0])).read()
        assert "## Entities" in content
        assert "[[SQLite]]" in content


class TestMarkdownEntityFiles:
    def test_backlinks(self, store, tmp_path):
        _seed_data(store)
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-export")
        renderer.render(output_dir=out)

        entity_file = os.path.join(out, "entities", "sqlite.md")
        assert os.path.exists(entity_file)
        content = open(entity_file).read()
        assert "# SQLite" in content
        assert "## Mentioned In" in content
        assert "m1" in content

    def test_relationships_table(self, store, tmp_path):
        _seed_data(store)
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-export")
        renderer.render(output_dir=out)

        entity_file = os.path.join(out, "entities", "sqlite.md")
        content = open(entity_file).read()
        assert "## Relationships" in content
        assert "related_to" in content


class TestMarkdownMeta:
    def test_export_meta(self, store, tmp_path):
        _seed_data(store)
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-export")
        renderer.render(output_dir=out)

        meta = open(os.path.join(out, "_export_meta.md")).read()
        assert "# Export Metadata" in meta
        assert "Memories" in meta
        assert "Entities" in meta

    def test_relationships_file(self, store, tmp_path):
        _seed_data(store)
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-export")
        renderer.render(output_dir=out)

        rel_file = os.path.join(out, "graph", "relationships.md")
        content = open(rel_file).read()
        assert "# Relationships" in content
        assert "related_to" in content


class TestMarkdownFiltered:
    def test_filtered_export(self, store, tmp_path):
        _seed_data(store)
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-filtered")
        filters = ExportFilter(project="lore")
        result = renderer.render(output_dir=out, filters=filters)
        assert result.memories == 1
        # Only code subdir should have files
        code_files = os.listdir(os.path.join(out, "memories", "code"))
        assert len(code_files) == 1
        assert not os.path.exists(os.path.join(out, "memories", "lesson"))


class TestMarkdownEmptyDatabase:
    def test_empty_database(self, store, tmp_path):
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-empty")
        result = renderer.render(output_dir=out)
        assert result.memories == 0
        assert os.path.isfile(os.path.join(out, "_export_meta.md"))


class TestMarkdownUnicode:
    def test_unicode_filenames(self, store, tmp_path):
        store.save(Memory(
            id="m-uni", content="日本語テスト 🎉 content here",
            type="general",
            created_at="2026-01-01", updated_at="2026-01-01",
        ))
        renderer = MarkdownRenderer(store)
        out = str(tmp_path / "md-unicode")
        result = renderer.render(output_dir=out)
        assert result.memories == 1
        general_dir = os.path.join(out, "memories", "general")
        files = os.listdir(general_dir)
        assert len(files) == 1


class TestMarkdownFormatBoth:
    def test_format_both(self, tmp_path):
        from lore import Lore
        db = str(tmp_path / "lore.db")
        lore = Lore(store=MemoryStore())
        lore.remember("both format test", type="code")
        json_out = str(tmp_path / "export.json")
        lore.export_data(format="both", output=json_out)
        lore.close()
        # JSON file exists
        assert os.path.exists(json_out)
        # Markdown dir exists (derived from json path)
        md_dir = json_out.rsplit(".", 1)[0]
        assert os.path.isdir(md_dir)
