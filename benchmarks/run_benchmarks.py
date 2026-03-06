"""
Lore v0.6.0 Performance Benchmarks

Measures latency for core SDK operations and reports median / p95.

Usage:
    python benchmarks/run_benchmarks.py [--store sqlite|memory] [--output docs/benchmarks.md]

Stores:
    memory  (default) — in-memory store, isolates SDK overhead
    sqlite  — SQLite-backed store in a temp directory

Embedding:
    Uses the real LocalEmbedder for realistic embedding benchmarks.
    Falls back to a stub embedder if ONNX runtime is not installed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# Ensure the repo's src/ is importable when running from the repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from lore import Lore
from lore.store.memory import MemoryStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_DIM = 384
_ITERATIONS = 10


def _stub_embed(text: str) -> List[float]:
    """Deterministic hash-based embedding (fallback when ONNX unavailable)."""
    raw: List[int] = []
    seed = text.encode()
    while len(raw) < _DIM:
        seed = hashlib.sha256(seed).digest()
        raw.extend(seed)
    vec = [(b / 255.0) * 2 - 1 for b in raw[:_DIM]]
    norm = max(sum(v * v for v in vec) ** 0.5, 1e-9)
    return [v / norm for v in vec]


@dataclass
class BenchResult:
    name: str
    iterations: int
    median_ms: float
    p95_ms: float
    target_ms: Optional[float] = None

    @property
    def pass_target(self) -> Optional[bool]:
        if self.target_ms is None:
            return None
        return self.median_ms <= self.target_ms


def _percentile(values: List[float], pct: float) -> float:
    """Simple percentile (nearest-rank)."""
    s = sorted(values)
    k = int(len(s) * pct / 100)
    k = min(k, len(s) - 1)
    return s[k]


def _bench(name: str, fn: Callable[[], object], iterations: int = _ITERATIONS,
           target_ms: Optional[float] = None) -> BenchResult:
    """Run *fn* for *iterations* and collect timing."""
    times: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        times.append(elapsed_ms)
    median = _percentile(times, 50)
    p95 = _percentile(times, 95)
    return BenchResult(name=name, iterations=iterations, median_ms=median,
                       p95_ms=p95, target_ms=target_ms)


# ---------------------------------------------------------------------------
# Benchmark definitions
# ---------------------------------------------------------------------------

def _make_lore(store_type: str, tmpdir: str, embed_fn) -> Lore:
    """Create a Lore instance for benchmarking."""
    if store_type == "sqlite":
        db_path = os.path.join(tmpdir, "bench.db")
        return Lore(db_path=db_path, embedding_fn=embed_fn, redact=False)
    else:
        return Lore(store=MemoryStore(), embedding_fn=embed_fn, redact=False)


def _seed_memories(lore: Lore, n: int) -> List[str]:
    """Populate *n* memories and return their IDs."""
    ids = []
    for i in range(n):
        mid = lore.remember(
            f"Benchmark memory #{i}: lorem ipsum performance test data "
            f"about topic-{i % 20} with details variant-{i}",
            type="general",
            tier="long",
            tags=[f"tag-{i % 5}"],
        )
        ids.append(mid)
    return ids


def run_benchmarks(store_type: str = "memory") -> List[BenchResult]:
    tmpdir = tempfile.mkdtemp(prefix="lore_bench_")
    results: List[BenchResult] = []

    # Resolve embedding function: prefer real LocalEmbedder
    embed_fn = _stub_embed
    using_real_embedder = False
    try:
        from lore.embed.local import LocalEmbedder
        embedder = LocalEmbedder()
        embedder.embed("warmup")  # trigger model download + load
        embed_fn = embedder.embed
        using_real_embedder = True
    except Exception:
        pass

    print(f"Store:     {store_type}")
    print(f"Embedder:  {'LocalEmbedder (ONNX)' if using_real_embedder else 'stub (hash-based)'}")
    print(f"Iterations per benchmark: {_ITERATIONS}")
    print()

    try:
        # -- Benchmark 1: remember() (no LLM) -----------------------------
        lore = _make_lore(store_type, tmpdir, embed_fn)
        counter = [0]

        def bench_remember():
            counter[0] += 1
            lore.remember(f"Bench memory {counter[0]}: some content about topic X")

        results.append(_bench("remember() — store + embed", bench_remember, target_ms=50))
        lore.close()

        # -- Benchmark 2: recall() over 100 memories ----------------------
        lore = _make_lore(store_type, tmpdir, embed_fn)
        _seed_memories(lore, 100)

        def bench_recall_100():
            lore.recall("performance benchmark query", limit=5)

        results.append(_bench("recall() — 100 memories", bench_recall_100, target_ms=100))
        lore.close()

        # -- Benchmark 3: recall() over 1000 memories ---------------------
        lore = _make_lore(store_type, tmpdir, embed_fn)
        _seed_memories(lore, 1000)

        def bench_recall_1000():
            lore.recall("performance benchmark query", limit=5)

        results.append(_bench("recall() — 1,000 memories", bench_recall_1000, target_ms=500))

        # -- Benchmark 4: as_prompt() over 100 memories --------------------
        # (reuse the 1000-memory instance)
        def bench_as_prompt():
            lore.as_prompt("CI/CD best practices", format="xml", limit=100, max_chars=50000)

        results.append(_bench("as_prompt() — format 100", bench_as_prompt, target_ms=500))

        # -- Benchmark 5: list_memories() over 1000 memories ---------------
        def bench_list():
            lore.list_memories(limit=1000)

        results.append(_bench("list_memories() — 1,000", bench_list, target_ms=100))

        # -- Benchmark 6: stats() over 1000 memories ----------------------
        def bench_stats():
            lore.stats()

        results.append(_bench("stats() — 1,000 memories", bench_stats, target_ms=50))
        lore.close()

        # -- Benchmark 7: embedding generation (500-word text) -------------
        long_text = " ".join(
            [f"word{i}" for i in range(500)]
            + ["The quick brown fox jumps over the lazy dog."] * 10
        )

        def bench_embed():
            embed_fn(long_text)

        results.append(_bench("embed() — 500-word text", bench_embed, target_ms=50))

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_table(results: List[BenchResult]) -> str:
    lines = [
        "| Benchmark | Median (ms) | P95 (ms) | Target (ms) | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for r in results:
        target = f"{r.target_ms:.1f}" if r.target_ms is not None else "-"
        if r.pass_target is None:
            status = "-"
        elif r.pass_target:
            status = "PASS"
        else:
            status = "FAIL"
        lines.append(
            f"| {r.name} | {r.median_ms:.2f} | {r.p95_ms:.2f} | {target} | {status} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lore v0.6.0 performance benchmarks")
    parser.add_argument("--store", choices=["sqlite", "memory"], default="memory",
                        help="Store backend (default: memory)")
    parser.add_argument("--output", type=str, default=None,
                        help="Write Markdown results to this file")
    args = parser.parse_args()

    print("=" * 60)
    print("  Lore v0.6.0 Performance Benchmarks")
    print("=" * 60)
    print()

    results = run_benchmarks(store_type=args.store)

    print()
    table = format_table(results)
    print(table)

    failed = [r for r in results if r.pass_target is False]
    if failed:
        print(f"\n{len(failed)} benchmark(s) exceeded target latency.")
    else:
        print("\nAll benchmarks within target latency.")

    if args.output:
        header = (
            f"# Lore v0.6.0 Benchmarks\n\n"
            f"Store: `{args.store}` | "
            f"Iterations: {_ITERATIONS}\n\n"
        )
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            f.write(header + table + "\n")
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
