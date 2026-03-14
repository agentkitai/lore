"""Performance test for E2: Recent Activity — S-PERF."""

from __future__ import annotations

import struct
import time
from datetime import datetime, timedelta, timezone
from typing import List

from lore.lore import Lore
from lore.store.memory import MemoryStore
from lore.types import Memory


def _stub_embed(text: str) -> List[float]:
    return [0.1] * 384


class TestRecentActivityPerformance:
    def test_500_memories_under_200ms(self, tmp_path):
        """Insert 500 memories, call recent_activity, assert latency < 500ms (CI-safe)."""
        str(tmp_path / "perf.db")
        store = MemoryStore()

        now = datetime.now(timezone.utc)
        embedding = struct.pack("384f", *([0.1] * 384))

        for i in range(500):
            created = (now - timedelta(hours=i * 0.04)).isoformat()
            m = Memory(
                id=f"m{i:04d}",
                content=f"Memory content number {i} with some details about work done",
                type="general" if i % 3 else "lesson",
                tier="long",
                project=f"project-{i % 5}",
                created_at=created,
                updated_at=created,
                embedding=embedding,
            )
            store.save(m)

        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lore._store = store

        start = time.monotonic()
        result = lore.recent_activity(hours=24, max_memories=200)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert result.total_count > 0
        # CI-safe: 500ms (local should be <200ms)
        assert elapsed_ms < 500, f"recent_activity took {elapsed_ms:.1f}ms, expected <500ms"
