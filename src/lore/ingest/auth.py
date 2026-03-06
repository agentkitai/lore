"""Ingest-specific API key validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class IngestAuthContext:
    """Authentication context for ingestion requests."""

    key_id: str
    org_id: str
    project: Optional[str]
    allowed_sources: Optional[List[str]] = None
    rate_limit: Optional[int] = None


def validate_ingest_key(
    api_key: str,
    source: str,
    *,
    scopes: Optional[List[str]] = None,
    allowed_sources: Optional[List[str]] = None,
    project: Optional[str] = None,
    key_id: str = "",
    org_id: str = "",
    rate_limit: Optional[int] = None,
) -> IngestAuthContext:
    """Build IngestAuthContext from pre-validated key data.

    The actual key validation is done by the server's auth middleware.
    This function checks ingest-specific constraints:
    1. Key has 'ingest' scope
    2. Key is authorized for the requested source adapter
    """
    if scopes and "ingest" not in scopes:
        raise PermissionError("Key does not have ingest scope")

    if allowed_sources and source not in allowed_sources:
        raise PermissionError(f"Key not authorized for source: {source}")

    return IngestAuthContext(
        key_id=key_id,
        org_id=org_id,
        project=project,
        allowed_sources=allowed_sources,
        rate_limit=rate_limit,
    )
