"""Tests for Memory dataclass."""

from lore.types import Memory, RecallResult


def test_memory_creation_minimal():
    memory = Memory(
        id="abc", content="some knowledge",
        created_at="t", updated_at="t",
    )
    assert memory.id == "abc"
    assert memory.content == "some knowledge"
    assert memory.type == "general"
    assert memory.tags == []
    assert memory.confidence == 1.0
    assert memory.upvotes == 0
    assert memory.metadata is None
    assert memory.ttl is None
    assert memory.expires_at is None


def test_memory_creation_full():
    memory = Memory(
        id="abc",
        content="Always use exponential backoff",
        type="lesson",
        tags=["a", "b"],
        metadata={"problem": "rate limiting", "resolution": "backoff"},
        source="agent-1",
        project="proj",
        embedding=b"\x00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        ttl=3600,
        expires_at="2026-01-01T01:00:00+00:00",
        confidence=0.9,
        upvotes=3,
        downvotes=1,
    )
    assert memory.type == "lesson"
    assert memory.tags == ["a", "b"]
    assert memory.metadata == {"problem": "rate limiting", "resolution": "backoff"}
    assert memory.ttl == 3600
    assert memory.confidence == 0.9


def test_memory_default_confidence_is_1():
    memory = Memory(id="x", content="test")
    assert memory.confidence == 1.0


def test_memory_type_default():
    memory = Memory(id="x", content="test")
    assert memory.type == "general"


def test_recall_result():
    memory = Memory(id="x", content="test")
    result = RecallResult(memory=memory, score=0.85)
    assert result.score == 0.85
    assert result.memory.content == "test"
