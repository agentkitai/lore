"""
Lore v0.6.0 — Memory Consolidation Demo

Creates several related memories, shows stats, then runs a consolidation
dry-run to demonstrate duplicate detection and grouping.

Usage:
    python examples/consolidation_demo.py
"""

from __future__ import annotations

import asyncio
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
    print("Lore v0.6.0 — Memory Consolidation Demo\n")

    lore = Lore(store=MemoryStore(), embedding_fn=stub_embed, redact=False)

    # --- Store related memories that could be consolidated ----------------
    print("[1] Storing 10 related memories about error handling...")

    memories = [
        "Always use try/except around external API calls",
        "Wrap HTTP requests in try/except to catch network errors",
        "External API calls should be wrapped in error handlers",
        "Use circuit breakers for flaky external services",
        "Implement circuit breaker pattern for unreliable APIs",
        "Set timeouts on all HTTP client calls (default 30s)",
        "HTTP client timeouts should be 30 seconds max",
        "Log full stack traces for unexpected exceptions",
        "Capture and log stack traces when exceptions occur",
        "Use structured error responses with error codes and messages",
    ]

    ids = []
    for content in memories:
        mid = lore.remember(content, type="lesson", tier="long", tags=["error-handling"])
        ids.append(mid)
        print(f"  [{mid[:8]}..] {content[:60]}")

    # --- Show stats before consolidation ----------------------------------
    print("\n[2] Stats before consolidation:")
    s = lore.stats()
    print(f"  Total memories: {s.total}")
    print(f"  By type: {dict(s.by_type)}")
    print(f"  By tier: {dict(s.by_tier)}")

    # --- Run consolidation dry-run ----------------------------------------
    print("\n[3] Running consolidation (dry_run=True)...")
    try:
        result = asyncio.run(lore.consolidate(dry_run=True))
        print(f"  Groups found:          {result.groups_found}")
        print(f"  Memories consolidated:  {result.memories_consolidated}")
        print(f"  Duplicates merged:      {result.duplicates_merged}")
        print(f"  New memories created:   {result.memories_created}")
        print(f"  Dry run:                {result.dry_run}")

        if result.groups:
            print(f"\n  Consolidation groups ({len(result.groups)}):")
            for i, group in enumerate(result.groups, 1):
                strategy = group.get("strategy", "unknown")
                mem_ids = group.get("memory_ids", [])
                print(f"    Group {i}: strategy={strategy}, members={len(mem_ids)}")
                for gid in mem_ids[:3]:
                    mem = lore.get(gid)
                    if mem:
                        print(f"      - {mem.content[:55]}")
                if len(mem_ids) > 3:
                    print(f"      ... and {len(mem_ids) - 3} more")
        else:
            print("\n  No consolidation groups found (stub embeddings are not semantic).")
            print("  With real embeddings, similar memories would be grouped.")

    except Exception as e:
        print(f"  Consolidation error: {type(e).__name__}: {e}")
        print("  (This is expected if the consolidation engine requires additional setup)")

    # --- Show stats after (unchanged since dry_run) -----------------------
    print("\n[4] Stats after dry-run (unchanged):")
    s2 = lore.stats()
    print(f"  Total memories: {s2.total}")

    lore.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
