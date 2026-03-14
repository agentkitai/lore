"""Export schema version, validation, and content-hash integrity.

The content hash covers only the ``data`` object inside the export
envelope — not the envelope metadata (exported_at, lore_version, etc.).
This ensures the hash is stable across re-exports of the same data.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

EXPORT_SCHEMA_VERSION = 1


def validate_schema_version(version: int) -> None:
    """Accept current or older schema versions; reject newer ones.

    Raises ``ValueError`` if the export was produced by a newer Lore
    version with an incompatible schema.
    """
    if version > EXPORT_SCHEMA_VERSION:
        raise ValueError(
            f"Export schema version {version} is newer than this Lore "
            f"installation supports (max {EXPORT_SCHEMA_VERSION}). "
            f"Please upgrade Lore before importing this file."
        )


def compute_content_hash(data: Dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash of the data object.

    Returns a string in the form ``sha256:<hex>``.
    """
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def verify_content_hash(export_dict: Dict[str, Any]) -> None:
    """Verify the content hash in an export envelope.

    Raises ``ValueError`` if the hash does not match.
    Silently passes if the export has no ``content_hash`` field (legacy).
    """
    stored_hash = export_dict.get("content_hash")
    if stored_hash is None:
        return  # Legacy export — skip verification

    data = export_dict.get("data", {})
    computed = compute_content_hash(data)
    if computed != stored_hash:
        raise ValueError(
            f"Content hash mismatch: expected {stored_hash}, "
            f"computed {computed}. The export file may be corrupted."
        )
