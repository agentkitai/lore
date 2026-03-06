"""
Lore v0.6.0 — Full Cognitive Pipeline Demo

Demonstrates the complete remember -> recall -> as_prompt -> stats workflow
using an in-memory store (no database or model downloads required).

Usage:
    python examples/full_pipeline.py
"""

from __future__ import annotations

import hashlib
from typing import List

from lore import Lore
from lore.store.memory import MemoryStore

# ---------------------------------------------------------------------------
# Stub embedding: deterministic hash-based, no model download needed
# ---------------------------------------------------------------------------
_DIM = 384


def stub_embed(text: str) -> List[float]:
    """Hash-based embedding for demo purposes (not semantic)."""
    # Generate enough bytes by chaining hashes, convert to [-1, 1] range
    raw: List[int] = []
    seed = text.encode()
    while len(raw) < _DIM:
        seed = hashlib.sha256(seed).digest()
        raw.extend(seed)
    vec = [(b / 255.0) * 2 - 1 for b in raw[:_DIM]]
    norm = max(sum(v * v for v in vec) ** 0.5, 1e-9)
    return [v / norm for v in vec]


def main() -> None:
    print("=" * 60)
    print("  Lore v0.6.0 — Full Cognitive Pipeline Demo")
    print("=" * 60)

    lore = Lore(store=MemoryStore(), embedding_fn=stub_embed, redact=False)

    # --- 1. Remember: store memories across tiers and types ----------------
    print("\n[1] Storing memories across tiers...")

    m1 = lore.remember("Use exponential backoff for retries", type="convention", tier="long", tags=["resilience"])
    m2 = lore.remember("The /users endpoint requires auth header", type="fact", tier="short", tags=["api", "auth"])
    m3 = lore.remember("Current debug: investigate OOM in worker pod", type="debug", tier="working", tags=["k8s"])
    m4 = lore.remember("Team prefers Ruff over Black for formatting", type="preference", tier="long", tags=["tooling"])
    m5 = lore.remember("Always validate webhook signatures before processing", type="lesson", tier="long", tags=["security"])

    print(f"  Stored 5 memories: {m1[:8]}.. {m2[:8]}.. {m3[:8]}.. {m4[:8]}.. {m5[:8]}..")

    # --- 2. Recall: semantic search ----------------------------------------
    print("\n[2] Recalling memories...")

    results = lore.recall("how to handle retries", limit=3)
    print(f"  Query: 'how to handle retries' -> {len(results)} results")
    for r in results:
        print(f"    [{r.score:.3f}] ({r.memory.type}/{r.memory.tier}) {r.memory.content[:60]}")

    # --- 3. Tier-filtered recall -------------------------------------------
    print("\n[3] Tier-filtered recall (long-term only)...")
    long_results = lore.recall("formatting tools", tier="long", limit=3)
    for r in long_results:
        print(f"    [{r.score:.3f}] {r.memory.content[:60]}")

    # --- 4. Tag-filtered recall --------------------------------------------
    print("\n[4] Tag-filtered recall (tag='security')...")
    sec_results = lore.recall("webhook", tags=["security"], limit=3)
    for r in sec_results:
        print(f"    [{r.score:.3f}] tags={r.memory.tags} {r.memory.content[:60]}")

    # --- 5. as_prompt: format for LLM context ------------------------------
    print("\n[5] Formatting recall results as LLM prompts...")

    for fmt in ("xml", "markdown", "raw"):
        prompt = lore.as_prompt("retry strategy", format=fmt, limit=2, max_chars=500)
        lines = prompt.strip().split("\n")
        preview = "\n    ".join(lines[:4])
        print(f"\n  [{fmt}] ({len(prompt)} chars):")
        print(f"    {preview}")
        if len(lines) > 4:
            print(f"    ... ({len(lines) - 4} more lines)")

    # --- 6. Upvote / downvote ---------------------------------------------
    print("\n[6] Voting on memories...")
    lore.upvote(m1)
    lore.upvote(m1)
    lore.downvote(m3)
    mem1 = lore.get(m1)
    mem3 = lore.get(m3)
    print(f"  Memory {m1[:8]}.. upvotes={mem1.upvotes} importance={mem1.importance_score:.2f}")
    print(f"  Memory {m3[:8]}.. downvotes={mem3.downvotes} importance={mem3.importance_score:.2f}")

    # --- 7. Stats ----------------------------------------------------------
    print("\n[7] Memory statistics...")
    s = lore.stats()
    print(f"  Total: {s.total}")
    print(f"  By type: {dict(s.by_type)}")
    print(f"  By tier: {dict(s.by_tier)}")
    print(f"  Oldest: {s.oldest}")
    print(f"  Newest: {s.newest}")

    # --- 8. Forget ---------------------------------------------------------
    print("\n[8] Forgetting a memory...")
    lore.forget(m3)
    print(f"  Deleted {m3[:8]}.. (working-tier debug note)")
    s2 = lore.stats()
    print(f"  Total now: {s2.total}")

    lore.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
