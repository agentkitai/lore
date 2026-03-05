"""Integration tests for HttpStore against a live Lore server.

Requires:
    - Docker Compose stack running: docker compose up -d
    - Server at: http://localhost:8765
    - API key: set LORE_API_KEY env var or use default test key

Run with:
    pytest -m integration tests/test_http_store_integration.py -v
"""

from __future__ import annotations

import os
import struct
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from lore.store.http import HttpStore
from lore.types import Memory

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_API_URL = os.environ.get("LORE_API_URL", "http://localhost:8765")
_API_KEY = os.environ.get(
    "LORE_API_KEY", "lore_sk_570ce9f86812d86689c3ad45739b9ba0"
)


def _server_available() -> bool:
    try:
        resp = httpx.get(f"{_API_URL}/health", timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _server_available(),
        reason=f"Lore server not available at {_API_URL}",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store():
    """Create an HttpStore connected to the live server with cleanup."""
    s = HttpStore(api_url=_API_URL, api_key=_API_KEY)
    created_ids: list[str] = []
    s._test_created_ids = created_ids  # type: ignore[attr-defined]
    yield s
    # Cleanup: delete all test-created memories
    for mid in created_ids:
        try:
            s.delete(mid)
        except Exception:
            pass
    s.close()


def _save_test_memory(store: HttpStore, **overrides) -> Memory:
    """Save a memory and track its ID for cleanup."""
    defaults = dict(
        id=f"test-{uuid.uuid4().hex[:12]}",
        content=f"Integration test memory {uuid.uuid4().hex[:8]}",
        type="general",
        context="integration test",
        tags=["_test", "integration"],
        metadata={"test": True},
        source="test",
        project="integration-tests",
        embedding=struct.pack("384f", *([0.1] * 384)),
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.9,
    )
    defaults.update(overrides)
    mem = Memory(**defaults)
    store.save(mem)
    store._test_created_ids.append(mem.id)  # type: ignore[attr-defined]
    return mem


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFullCrudCycle:
    def test_save_get_update_delete(self, store: HttpStore):
        # Save
        mem = _save_test_memory(store, content="CRUD test content")
        assert mem.id  # server overwrites ID

        # Get
        fetched = store.get(mem.id)
        assert fetched is not None
        assert fetched.content == "CRUD test content"
        assert abs(fetched.confidence - 0.9) < 0.01  # float32 precision

        # Update
        fetched.confidence = 0.5
        fetched.tags = ["_test", "updated"]
        result = store.update(fetched)
        assert result is True

        # Verify update
        updated = store.get(mem.id)
        assert updated is not None
        assert updated.confidence == 0.5
        assert "updated" in updated.tags

        # Delete
        deleted = store.delete(mem.id)
        assert deleted is True

        # Verify delete
        gone = store.get(mem.id)
        assert gone is None

        # Double delete returns False
        assert store.delete(mem.id) is False

        # Remove from cleanup list since already deleted
        store._test_created_ids.remove(mem.id)  # type: ignore[attr-defined]


class TestSaveAndSearch:
    def test_remember_and_recall(self, store: HttpStore):
        # Save a memory with embedding
        mem = _save_test_memory(
            store,
            content="Always use exponential backoff for rate limiting",
            tags=["_test", "rate-limiting"],
        )

        # Search with the same embedding vector
        results = store.search(
            embedding=[0.1] * 384,
            project="integration-tests",
            limit=5,
        )
        assert len(results) > 0
        # Our memory should appear in results
        result_ids = [r.memory.id for r in results]
        assert mem.id in result_ids

        # Verify score is present
        matching = [r for r in results if r.memory.id == mem.id]
        assert matching[0].score > 0


class TestRoundTripFidelity:
    def test_all_fields_preserved(self, store: HttpStore):
        mem = _save_test_memory(
            store,
            content="Round trip fidelity test",
            type="lesson",
            context="testing context",
            tags=["_test", "fidelity", "round-trip"],
            metadata={"custom_key": "custom_value", "number": 42},
            source="fidelity-test",
            project="integration-tests",
            confidence=0.75,
        )

        fetched = store.get(mem.id)
        assert fetched is not None
        assert fetched.content == "Round trip fidelity test"
        assert fetched.type == "lesson"
        assert fetched.context == "testing context"
        assert "_test" in fetched.tags
        assert "fidelity" in fetched.tags
        assert fetched.metadata is not None
        assert fetched.metadata.get("custom_key") == "custom_value"
        assert fetched.metadata.get("number") == 42
        assert fetched.source == "fidelity-test"
        assert fetched.project == "integration-tests"
        assert fetched.confidence == 0.75
        assert fetched.created_at
        assert fetched.updated_at
        assert fetched.embedding is None  # server doesn't return embeddings


class TestCrossInstanceVisibility:
    def test_two_stores_see_same_data(self, store: HttpStore):
        mem = _save_test_memory(store, content="Cross-instance visibility test")

        # Create a second store instance
        store2 = HttpStore(api_url=_API_URL, api_key=_API_KEY)
        try:
            fetched = store2.get(mem.id)
            assert fetched is not None
            assert fetched.content == "Cross-instance visibility test"
        finally:
            store2.close()


class TestUpvoteDownvote:
    def test_atomic_upvote(self, store: HttpStore):
        mem = _save_test_memory(store, content="Vote test")

        store.upvote(mem.id)
        fetched = store.get(mem.id)
        assert fetched is not None
        assert fetched.upvotes == 1

        store.upvote(mem.id)
        fetched = store.get(mem.id)
        assert fetched.upvotes == 2

    def test_atomic_downvote(self, store: HttpStore):
        mem = _save_test_memory(store, content="Downvote test")

        store.downvote(mem.id)
        fetched = store.get(mem.id)
        assert fetched is not None
        assert fetched.downvotes == 1

    def test_upvote_nonexistent_raises(self, store: HttpStore):
        from lore.exceptions import MemoryNotFoundError
        with pytest.raises(MemoryNotFoundError):
            store.upvote("nonexistent-id-12345")
