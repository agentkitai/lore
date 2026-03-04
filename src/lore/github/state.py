"""Sync state persistence for GitHub Sync.

Tracks the last sync point per repo so incremental syncs only fetch new data.
State is stored in ``~/.lore/sync_state.json``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


_STATE_FILE = os.path.join(os.path.expanduser("~"), ".lore", "sync_state.json")


def _default_path() -> str:
    return _STATE_FILE


def _load_all(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or _default_path()
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _save_all(data: Dict[str, Any], path: Optional[str] = None) -> None:
    path = path or _default_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_sync_state(repo: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return sync state for *repo*, or ``None`` if never synced."""
    return _load_all(path).get(repo)


def update_sync_state(
    repo: str,
    *,
    last_sync: Optional[str] = None,
    last_pr: Optional[int] = None,
    last_issue: Optional[int] = None,
    last_release: Optional[str] = None,
    path: Optional[str] = None,
) -> None:
    """Merge updated sync markers into the persisted state for *repo*."""
    data = _load_all(path)
    entry = data.get(repo, {})
    if last_sync is not None:
        entry["last_sync"] = last_sync
    if last_pr is not None:
        entry["last_pr"] = last_pr
    if last_issue is not None:
        entry["last_issue"] = last_issue
    if last_release is not None:
        entry["last_release"] = last_release
    data[repo] = entry
    _save_all(data, path)


def list_synced_repos(path: Optional[str] = None) -> Dict[str, Any]:
    """Return all synced repos and their state."""
    return _load_all(path)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
