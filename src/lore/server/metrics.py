"""Prometheus metrics for Lore server."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional


class _Counter:
    """Simple counter metric."""

    def __init__(self, name: str, help_text: str, labels: Optional[List[str]] = None):
        self.name = name
        self.help_text = help_text
        self.labels = labels or []
        self._values: Dict[tuple, float] = defaultdict(float)

    def inc(self, amount: float = 1.0, **kwargs: str) -> None:
        key = tuple(kwargs.get(l, "") for l in self.labels)
        self._values[key] += amount

    def collect(self) -> str:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} counter"]
        if not self._values:
            return "\n".join(lines)
        for key, val in sorted(self._values.items()):
            if self.labels:
                label_str = ",".join(f'{l}="{v}"' for l, v in zip(self.labels, key))
                lines.append(f"{self.name}{{{label_str}}} {val}")
            else:
                lines.append(f"{self.name} {val}")
        return "\n".join(lines)


class _Histogram:
    """Simple histogram metric (just tracks count and sum for Prometheus)."""

    # Default buckets
    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"))

    def __init__(self, name: str, help_text: str, labels: Optional[List[str]] = None,
                 buckets: Optional[tuple] = None):
        self.name = name
        self.help_text = help_text
        self.labels = labels or []
        self.buckets = buckets or self.DEFAULT_BUCKETS
        self._observations: Dict[tuple, List[float]] = defaultdict(list)

    def observe(self, value: float, **kwargs: str) -> None:
        key = tuple(kwargs.get(l, "") for l in self.labels)
        self._observations[key].append(value)

    def collect(self) -> str:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} histogram"]
        for key, values in sorted(self._observations.items()):
            label_str = ""
            if self.labels:
                label_str = ",".join(f'{l}="{v}"' for l, v in zip(self.labels, key))

            # Compute buckets
            for b in self.buckets:
                count = sum(1 for v in values if v <= b)
                le = "+Inf" if b == float("inf") else str(b)
                if label_str:
                    lines.append(f'{self.name}_bucket{{{label_str},le="{le}"}} {count}')
                else:
                    lines.append(f'{self.name}_bucket{{le="{le}"}} {count}')

            total = sum(values)
            cnt = len(values)
            if label_str:
                lines.append(f"{self.name}_sum{{{label_str}}} {total}")
                lines.append(f"{self.name}_count{{{label_str}}} {cnt}")
            else:
                lines.append(f"{self.name}_sum {total}")
                lines.append(f"{self.name}_count {cnt}")
        return "\n".join(lines)


class _Gauge:
    """Simple gauge metric."""

    def __init__(self, name: str, help_text: str, labels: Optional[List[str]] = None):
        self.name = name
        self.help_text = help_text
        self.labels = labels or []
        self._values: Dict[tuple, float] = defaultdict(float)

    def set(self, value: float, **kwargs: str) -> None:
        key = tuple(kwargs.get(l, "") for l in self.labels)
        self._values[key] = value

    def collect(self) -> str:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} gauge"]
        for key, val in sorted(self._values.items()):
            if self.labels:
                label_str = ",".join(f'{l}="{v}"' for l, v in zip(self.labels, key))
                lines.append(f"{self.name}{{{label_str}}} {val}")
            else:
                lines.append(f"{self.name} {val}")
        return "\n".join(lines)


# ── Business Metrics ───────────────────────────────────────────────

memories_saved_total = _Counter("lore_memories_saved_total", "Total memories saved")
recall_queries_total = _Counter("lore_recall_queries_total", "Total recall queries")
embedding_latency = _Histogram("lore_embedding_latency_seconds", "Embedding generation latency")
vector_search_latency = _Histogram("lore_vector_search_latency_seconds", "Vector search latency")
db_pool_size = _Gauge("lore_db_pool_size", "DB connection pool size")
db_pool_available = _Gauge("lore_db_pool_available", "DB connections available in pool")

# ── HTTP RED Metrics ───────────────────────────────────────────────

http_requests_total = _Counter("http_requests_total", "Total HTTP requests", ["method", "path", "status"])
http_request_duration = _Histogram("http_request_duration_seconds", "HTTP request duration", ["method", "path"])

# ── Registry ───────────────────────────────────────────────────────

ALL_METRICS = [
    memories_saved_total,
    recall_queries_total,
    embedding_latency,
    vector_search_latency,
    db_pool_size,
    db_pool_available,
    http_requests_total,
    http_request_duration,
]


def collect_all() -> str:
    """Collect all metrics in Prometheus text format."""
    # Update pool gauges
    try:
        from lore.server.db import _pool
        if _pool is not None:
            db_pool_size.set(float(_pool.get_size()))
            db_pool_available.set(float(_pool.get_idle_size()))
    except Exception:
        pass

    return "\n\n".join(m.collect() for m in ALL_METRICS) + "\n"
