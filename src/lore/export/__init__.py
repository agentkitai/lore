"""Export / Import / Snapshot — E5 Safety Net.

Public API:
    Exporter          — JSON + Markdown export engine
    Importer          — JSON import engine with dedup + hash verification
    SnapshotManager   — Create / list / delete / restore snapshots
    MarkdownRenderer  — Obsidian-compatible Markdown export
"""


def __getattr__(name: str):
    """Lazy imports to avoid circular / missing-class errors during development."""
    if name == "Exporter":
        from lore.export.exporter import Exporter
        return Exporter
    if name == "Importer":
        from lore.export.importer import Importer
        return Importer
    if name == "MarkdownRenderer":
        from lore.export.markdown import MarkdownRenderer
        return MarkdownRenderer
    if name == "SnapshotManager":
        from lore.export.snapshot import SnapshotManager
        return SnapshotManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Exporter", "Importer", "MarkdownRenderer", "SnapshotManager"]
