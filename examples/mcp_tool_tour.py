"""
Lore v0.6.0 — MCP Tool Tour

Walks through every SDK method that maps to an MCP tool, grouped by
category. Uses an in-memory store with a stub embedder so it runs
standalone with no database, model downloads, or LLM keys.

Usage:
    python examples/mcp_tool_tour.py
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import List

from lore import Lore
from lore.store.memory import MemoryStore

# ---------------------------------------------------------------------------
# Stub embedding
# ---------------------------------------------------------------------------
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


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def main() -> None:
    print("Lore v0.6.0 — MCP Tool Tour")
    print("Each section mirrors an MCP tool exposed by the Lore server.\n")

    lore = Lore(store=MemoryStore(), embedding_fn=stub_embed, redact=False)

    # ======================================================================
    # MEMORY MANAGEMENT (remember, recall, forget, list, stats, upvote/downvote)
    # ======================================================================
    section("MEMORY MANAGEMENT")

    # -- remember (MCP: remember) --
    print("\n>> remember(content, type, tier, tags, metadata)")
    ids = []
    samples = [
        ("Always pin Docker base images to a digest", "convention", "long", ["docker", "ci"]),
        ("Use structured logging with correlation IDs", "lesson", "long", ["observability"]),
        ("Current sprint goal: ship v0.6.0 features", "general", "short", ["sprint"]),
        ("Debugging: Redis connection pool exhaustion", "debug", "working", ["redis"]),
        ("Team prefers pytest-xdist for parallel tests", "preference", "long", ["testing"]),
    ]
    for content, typ, tier, tags in samples:
        mid = lore.remember(content, type=typ, tier=tier, tags=tags)
        ids.append(mid)
        print(f"  [{mid[:8]}..] ({typ}/{tier}) {content[:50]}")

    # -- recall (MCP: recall) --
    print("\n>> recall(query, limit, tier, type, tags)")
    results = lore.recall("docker image best practices", limit=3)
    print(f"  Found {len(results)} results for 'docker image best practices':")
    for r in results:
        print(f"    score={r.score:.3f}  {r.memory.content[:50]}")

    results_filtered = lore.recall("testing", type="preference", limit=2)
    print(f"  Filtered by type='preference': {len(results_filtered)} results")

    # -- forget (MCP: forget) --
    print("\n>> forget(memory_id)")
    removed = lore.forget(ids[3])  # the debug memory
    print(f"  Forgot {ids[3][:8]}.. -> {removed}")

    # -- list_memories (MCP: list_memories) --
    print("\n>> list_memories(type, tier, limit)")
    all_mems = lore.list_memories(limit=10)
    print(f"  Total listed: {len(all_mems)}")
    long_mems = lore.list_memories(tier="long")
    print(f"  Long-tier only: {len(long_mems)}")

    # -- stats (MCP: stats) --
    print("\n>> stats()")
    s = lore.stats()
    print(f"  total={s.total}  by_type={dict(s.by_type)}  by_tier={dict(s.by_tier)}")

    # -- upvote / downvote (MCP: upvote_memory, downvote_memory) --
    print("\n>> upvote(memory_id) / downvote(memory_id)")
    lore.upvote(ids[0])
    lore.upvote(ids[0])
    lore.downvote(ids[2])
    m0 = lore.get(ids[0])
    m2 = lore.get(ids[2])
    print(f"  {ids[0][:8]}.. upvotes={m0.upvotes}  importance={m0.importance_score:.2f}")
    print(f"  {ids[2][:8]}.. downvotes={m2.downvotes}  importance={m2.importance_score:.2f}")

    # ======================================================================
    # KNOWLEDGE / FACTS (extract_facts, list_facts, get_active_facts, conflicts)
    # ======================================================================
    section("KNOWLEDGE / FACTS")
    print("  (These features require LLM configuration for full operation)")

    # -- extract_facts (MCP: extract_facts) --
    print("\n>> extract_facts(text)")
    try:
        facts = lore.extract_facts("Python 3.12 added support for type parameter syntax.")
        print(f"  Extracted {len(facts)} facts")
        for f in facts:
            print(f"    ({f.subject}, {f.predicate}, {f.object})")
    except Exception as e:
        print(f"  Skipped (requires LLM): {type(e).__name__}")

    # -- get_active_facts (MCP: list_facts) --
    print("\n>> get_active_facts(subject)")
    facts = lore.get_active_facts()
    print(f"  Active facts: {len(facts)}")

    # -- list_conflicts (MCP: conflicts) --
    print("\n>> list_conflicts()")
    conflicts = lore.list_conflicts()
    print(f"  Conflict log entries: {len(conflicts)}")

    # ======================================================================
    # INTELLIGENCE (classify, enrich, as_prompt)
    # ======================================================================
    section("INTELLIGENCE")

    # -- classify (MCP: classify) --
    print("\n>> classify(text)")
    try:
        cls = lore.classify("Fix the null pointer exception in the auth module")
        print(f"  intent={cls.intent}  domain={cls.domain}  emotion={cls.emotion}")
        print(f"  confidence={cls.confidence}")
    except Exception as e:
        print(f"  Skipped: {type(e).__name__}: {e}")

    # -- enrich_memories (MCP: enrich) --
    print("\n>> enrich_memories(memory_ids)")
    try:
        result = lore.enrich_memories(memory_ids=[ids[0]])
        print(f"  Enrichment result: {result}")
    except Exception as e:
        print(f"  Skipped (requires LLM): {type(e).__name__}")

    # -- as_prompt (MCP: as_prompt) --
    print("\n>> as_prompt(query, format, max_tokens)")
    for fmt in ("xml", "markdown", "raw"):
        prompt = lore.as_prompt("CI/CD best practices", format=fmt, limit=2, max_chars=300)
        line_count = len(prompt.strip().split("\n"))
        print(f"  format={fmt}: {len(prompt)} chars, {line_count} lines")

    # ======================================================================
    # CONSOLIDATION (consolidate)
    # ======================================================================
    section("CONSOLIDATION")

    print("\n>> consolidate(dry_run=True)")
    try:
        result = asyncio.run(lore.consolidate(dry_run=True))
        print(f"  groups_found={result.groups_found}")
        print(f"  memories_consolidated={result.memories_consolidated}")
        print(f"  duplicates_merged={result.duplicates_merged}")
        print(f"  dry_run={result.dry_run}")
    except Exception as e:
        print(f"  Skipped: {type(e).__name__}: {e}")

    # ======================================================================
    # CLEANUP
    # ======================================================================
    section("CLEANUP")
    lore.close()
    print("  lore.close() called. Session ended.")

    print("\nTour complete.")


if __name__ == "__main__":
    main()
