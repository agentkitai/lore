"""Multi-source ingestion for Lore — normalize, dedup, and store from any source."""

from lore.ingest.adapters.base import NormalizedMessage, SourceAdapter

__all__ = [
    "NormalizedMessage",
    "SourceAdapter",
]


def __getattr__(name: str):
    """Lazy imports to avoid circular dependencies."""
    if name in ("IngestResult", "IngestionPipeline"):
        from lore.ingest.pipeline import IngestionPipeline, IngestResult
        return {"IngestResult": IngestResult, "IngestionPipeline": IngestionPipeline}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
