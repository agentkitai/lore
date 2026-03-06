"""
Lore v0.6.0 — Webhook / Multi-Source Ingestion Demo

Shows how to ingest content from different sources (Slack, Git, etc.)
with provenance metadata, then recall it with source tracking.

Usage:
    python examples/webhook_ingestion.py
"""

from __future__ import annotations

import hashlib
from typing import List

from lore import Lore
from lore.store.memory import MemoryStore

_DIM = 384


def stub_embed(text: str) -> List[float]:
    raw: List[int] = []
    seed = text.encode()
    while len(raw) < _DIM:
        seed = hashlib.sha256(seed).digest()
        raw.extend(seed)
    vec = [(b / 255.0) * 2 - 1 for b in raw[:_DIM]]
    norm = max(sum(v * v for v in vec) ** 0.5, 1e-9)
    return [v / norm for v in vec]


def main() -> None:
    print("Lore v0.6.0 — Webhook / Multi-Source Ingestion Demo\n")

    lore = Lore(store=MemoryStore(), embedding_fn=stub_embed, redact=False)

    # --- Simulate ingesting from Slack ------------------------------------
    print("[Slack] Ingesting a message from #engineering...")
    m1 = lore.remember(
        "We decided to migrate from REST to gRPC for inter-service calls",
        type="lesson",
        tier="long",
        tags=["architecture", "grpc"],
        source="slack",
        metadata={
            "source_info": {
                "platform": "slack",
                "channel": "#engineering",
                "author": "alice",
                "message_ts": "1709712000.000100",
            }
        },
    )
    print(f"  Stored as {m1[:8]}..")

    # --- Simulate ingesting from Git commit -------------------------------
    print("\n[Git] Ingesting a commit message...")
    m2 = lore.remember(
        "refactor: extract retry logic into shared middleware (reduces duplication in 12 handlers)",
        type="code",
        tier="long",
        tags=["refactor", "middleware"],
        source="git",
        metadata={
            "source_info": {
                "platform": "git",
                "repo": "acme/backend",
                "commit_sha": "a1b2c3d4",
                "author": "bob@acme.com",
                "branch": "main",
            }
        },
    )
    print(f"  Stored as {m2[:8]}..")

    # --- Simulate ingesting from a Telegram bot ---------------------------
    print("\n[Telegram] Ingesting a user note...")
    m3 = lore.remember(
        "Remember: the staging DB password rotates every 30 days",
        type="fact",
        tier="short",
        tags=["ops", "credentials"],
        source="telegram",
        metadata={
            "source_info": {
                "platform": "telegram",
                "chat_id": "-100123456",
                "from_user": "carol",
            }
        },
    )
    print(f"  Stored as {m3[:8]}..")

    # --- Recall and show provenance ---------------------------------------
    print("\n--- Recalling with provenance ---")
    results = lore.recall("retry middleware refactor", limit=3)
    for r in results:
        mem = r.memory
        src = (mem.metadata or {}).get("source_info", {})
        platform = src.get("platform", "unknown")
        print(f"\n  [{r.score:.3f}] source={mem.source or platform}")
        print(f"    content: {mem.content[:70]}")
        if src:
            print(f"    provenance: {src}")

    # --- List by source tag -----------------------------------------------
    print("\n--- Listing all memories (source tracking) ---")
    for mem in lore.list_memories():
        src = (mem.metadata or {}).get("source_info", {})
        print(f"  [{mem.source or 'n/a':10s}] {mem.content[:55]}  (from {src.get('platform', '?')})")

    lore.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
