"""Shared timestamp/JSON parsing helpers used across route modules."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional


def _parse_tags(raw) -> list:
    """Parse a tags column that may be a list, JSON string, or None."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    return list(raw)


def _parse_meta(raw) -> dict:
    """Parse a meta/JSONB column that may be a dict, JSON string, or None."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
    return dict(raw)


def _ts(val) -> Optional[str]:
    """Convert a datetime or value to an ISO-format string, or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)
