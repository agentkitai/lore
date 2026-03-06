"""Scenario 8 — Entity infrastructure via store directly."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lore.store.memory import MemoryStore
from lore.types import Entity, EntityMention, Relationship


class TestEntityCRUD:
    """Test entity save/get/list on the MemoryStore."""

    def test_save_and_get_entity(self, memory_store: MemoryStore) -> None:
        """save_entity + get_entity round-trips."""
        now = datetime.now(timezone.utc).isoformat()
        entity = Entity(
            id="ent-001", name="Python", entity_type="language",
            aliases=["python", "py"], description="Programming language",
            mention_count=5, created_at=now, updated_at=now,
        )
        memory_store.save_entity(entity)

        retrieved = memory_store.get_entity("ent-001")
        assert retrieved is not None
        assert retrieved.name == "Python"
        assert retrieved.entity_type == "language"
        assert "py" in retrieved.aliases

    def test_get_entity_by_name(self, memory_store: MemoryStore) -> None:
        """get_entity_by_name finds entity by exact name."""
        now = datetime.now(timezone.utc).isoformat()
        memory_store.save_entity(Entity(
            id="e1", name="Redis", entity_type="tool",
            created_at=now, updated_at=now,
        ))
        found = memory_store.get_entity_by_name("Redis")
        assert found is not None
        assert found.id == "e1"

    def test_get_entity_by_alias(self, memory_store: MemoryStore) -> None:
        """get_entity_by_alias finds entity by alias string."""
        now = datetime.now(timezone.utc).isoformat()
        memory_store.save_entity(Entity(
            id="e2", name="PostgreSQL", entity_type="tool",
            aliases=["postgres", "pg"], created_at=now, updated_at=now,
        ))
        found = memory_store.get_entity_by_alias("pg")
        assert found is not None
        assert found.name == "PostgreSQL"

    def test_list_entities(self, memory_store: MemoryStore) -> None:
        """list_entities returns all entities sorted by mention_count."""
        now = datetime.now(timezone.utc).isoformat()
        memory_store.save_entity(Entity(
            id="e1", name="A", entity_type="concept",
            mention_count=1, created_at=now, updated_at=now,
        ))
        memory_store.save_entity(Entity(
            id="e2", name="B", entity_type="tool",
            mention_count=10, created_at=now, updated_at=now,
        ))
        entities = memory_store.list_entities()
        assert len(entities) == 2
        # Sorted by mention_count descending
        assert entities[0].name == "B"

    def test_list_entities_by_type(self, memory_store: MemoryStore) -> None:
        """list_entities(entity_type=...) filters by type."""
        now = datetime.now(timezone.utc).isoformat()
        memory_store.save_entity(Entity(
            id="e1", name="Python", entity_type="language",
            created_at=now, updated_at=now,
        ))
        memory_store.save_entity(Entity(
            id="e2", name="Docker", entity_type="tool",
            created_at=now, updated_at=now,
        ))
        tools = memory_store.list_entities(entity_type="tool")
        assert len(tools) == 1
        assert tools[0].name == "Docker"

    def test_delete_entity_cascades(self, memory_store: MemoryStore) -> None:
        """Deleting an entity removes its relationships and mentions."""
        now = datetime.now(timezone.utc).isoformat()
        memory_store.save_entity(Entity(
            id="e1", name="A", entity_type="concept",
            created_at=now, updated_at=now,
        ))
        memory_store.save_entity(Entity(
            id="e2", name="B", entity_type="concept",
            created_at=now, updated_at=now,
        ))
        memory_store.save_relationship(Relationship(
            id="r1", source_entity_id="e1", target_entity_id="e2",
            rel_type="related_to", created_at=now, updated_at=now,
        ))
        memory_store.save_entity_mention(EntityMention(
            id="em1", entity_id="e1", memory_id="m1", created_at=now,
        ))

        memory_store.delete_entity("e1")
        assert memory_store.get_entity("e1") is None
        assert memory_store.list_relationships(entity_id="e1") == []
        assert memory_store.get_entity_mentions_for_entity("e1") == []


class TestRelationshipCRUD:
    """Test relationship save/get/list on the MemoryStore."""

    def test_save_and_get_relationship(self, memory_store: MemoryStore) -> None:
        """save_relationship + get_relationship round-trips."""
        now = datetime.now(timezone.utc).isoformat()
        rel = Relationship(
            id="r1", source_entity_id="e1", target_entity_id="e2",
            rel_type="depends_on", weight=0.8,
            created_at=now, updated_at=now,
        )
        memory_store.save_relationship(rel)

        retrieved = memory_store.get_relationship("r1")
        assert retrieved is not None
        assert retrieved.rel_type == "depends_on"
        assert retrieved.weight == 0.8

    def test_list_relationships_by_entity(self, memory_store: MemoryStore) -> None:
        """list_relationships(entity_id=...) returns relationships for that entity."""
        now = datetime.now(timezone.utc).isoformat()
        memory_store.save_relationship(Relationship(
            id="r1", source_entity_id="e1", target_entity_id="e2",
            rel_type="uses", created_at=now, updated_at=now,
        ))
        memory_store.save_relationship(Relationship(
            id="r2", source_entity_id="e3", target_entity_id="e1",
            rel_type="depends_on", created_at=now, updated_at=now,
        ))
        memory_store.save_relationship(Relationship(
            id="r3", source_entity_id="e4", target_entity_id="e5",
            rel_type="uses", created_at=now, updated_at=now,
        ))

        rels = memory_store.list_relationships(entity_id="e1")
        assert len(rels) == 2
        rel_ids = {r.id for r in rels}
        assert rel_ids == {"r1", "r2"}

    def test_list_relationships_by_type(self, memory_store: MemoryStore) -> None:
        """list_relationships(rel_type=...) filters by type."""
        now = datetime.now(timezone.utc).isoformat()
        memory_store.save_relationship(Relationship(
            id="r1", source_entity_id="e1", target_entity_id="e2",
            rel_type="uses", created_at=now, updated_at=now,
        ))
        memory_store.save_relationship(Relationship(
            id="r2", source_entity_id="e1", target_entity_id="e3",
            rel_type="depends_on", created_at=now, updated_at=now,
        ))

        uses = memory_store.list_relationships(rel_type="uses")
        assert len(uses) == 1
        assert uses[0].id == "r1"

    def test_get_relationships_from(self, memory_store: MemoryStore) -> None:
        """get_relationships_from returns outbound relationships."""
        now = datetime.now(timezone.utc).isoformat()
        memory_store.save_relationship(Relationship(
            id="r1", source_entity_id="e1", target_entity_id="e2",
            rel_type="uses", created_at=now, updated_at=now,
        ))
        rels = memory_store.get_relationships_from(["e1"])
        assert len(rels) == 1
        assert rels[0].target_entity_id == "e2"


class TestEntityMentionCRUD:
    """Test entity mention save/get on the MemoryStore."""

    def test_save_and_get_mentions_for_memory(self, memory_store: MemoryStore) -> None:
        """save_entity_mention + get_entity_mentions_for_memory works."""
        now = datetime.now(timezone.utc).isoformat()
        mention = EntityMention(
            id="em1", entity_id="e1", memory_id="m1",
            mention_type="explicit", confidence=0.9, created_at=now,
        )
        memory_store.save_entity_mention(mention)

        mentions = memory_store.get_entity_mentions_for_memory("m1")
        assert len(mentions) == 1
        assert mentions[0].entity_id == "e1"

    def test_save_and_get_mentions_for_entity(self, memory_store: MemoryStore) -> None:
        """get_entity_mentions_for_entity returns all mentions of an entity."""
        now = datetime.now(timezone.utc).isoformat()
        memory_store.save_entity_mention(EntityMention(
            id="em1", entity_id="e1", memory_id="m1", created_at=now,
        ))
        memory_store.save_entity_mention(EntityMention(
            id="em2", entity_id="e1", memory_id="m2", created_at=now,
        ))

        mentions = memory_store.get_entity_mentions_for_entity("e1")
        assert len(mentions) == 2

    def test_idempotent_mention(self, memory_store: MemoryStore) -> None:
        """Saving the same (entity_id, memory_id) twice is idempotent."""
        now = datetime.now(timezone.utc).isoformat()
        mention = EntityMention(
            id="em1", entity_id="e1", memory_id="m1", created_at=now,
        )
        memory_store.save_entity_mention(mention)
        memory_store.save_entity_mention(EntityMention(
            id="em1-dup", entity_id="e1", memory_id="m1", created_at=now,
        ))

        mentions = memory_store.get_entity_mentions_for_entity("e1")
        assert len(mentions) == 1
