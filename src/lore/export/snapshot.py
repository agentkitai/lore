"""Snapshot lifecycle management — create, list, delete, restore, prune."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from lore.types import ImportResult

_DEFAULT_SNAPSHOTS_DIR = os.path.expanduser("~/.lore/snapshots")
_DURATION_RE = re.compile(r"^(\d+)([dhwm])$")


def _parse_duration(s: str) -> timedelta:
    """Parse a human duration string like '30d', '4w', '6m'."""
    m = _DURATION_RE.match(s.strip())
    if not m:
        raise ValueError(f"Invalid duration: {s!r}. Use e.g. '30d', '4w', '6m'.")
    n, unit = int(m.group(1)), m.group(2)
    if unit == "d":
        return timedelta(days=n)
    if unit == "w":
        return timedelta(weeks=n)
    if unit == "m":
        return timedelta(days=n * 30)
    if unit == "h":
        return timedelta(hours=n)
    raise ValueError(f"Unknown unit: {unit}")


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f}{unit}" if unit != "B" else f"{nbytes}{unit}"
        nbytes /= 1024  # type: ignore[assignment]
    return f"{nbytes:.1f}TB"


class SnapshotManager:
    """Manages snapshot lifecycle: create, list, delete, restore, prune."""

    def __init__(
        self,
        lore: Any,
        snapshots_dir: Optional[str] = None,
        max_snapshots: int = 50,
    ) -> None:
        self._lore = lore
        self._dir = Path(snapshots_dir or _DEFAULT_SNAPSHOTS_DIR)
        self._max_snapshots = max_snapshots

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def create(self) -> Dict[str, Any]:
        """Create a new snapshot (full JSON export)."""
        self._ensure_dir()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        name = ts
        output = str(self._dir / f"{name}.json")

        result = self._lore.export_data(format="json", output=output)

        # Set restrictive permissions
        try:
            os.chmod(output, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass

        # Auto-prune
        self._auto_prune()

        size = os.path.getsize(output)
        return {
            "name": name,
            "path": output,
            "memories": result.memories,
            "size_bytes": size,
            "size_human": _human_size(size),
        }

    def list(self) -> List[Dict[str, Any]]:
        """List snapshots sorted newest first."""
        self._ensure_dir()
        snapshots: List[Dict[str, Any]] = []

        for f in sorted(self._dir.glob("*.json"), reverse=True):
            name = f.stem
            size = f.stat().st_size
            # Try to read counts from first bytes
            memories = "?"
            created_at = name
            try:
                with open(f, "r") as fh:
                    header = fh.read(1024)
                # Quick parse for counts
                if '"memories":' in header:
                    # Parse the full file for counts (from the counts object)
                    data = json.loads(f.read_text(encoding="utf-8"))
                    memories = data.get("counts", {}).get("memories", "?")
                    created_at = data.get("exported_at", name)
            except Exception:
                pass

            snapshots.append({
                "name": name,
                "created_at": created_at,
                "memories": memories,
                "size_bytes": size,
                "size_human": _human_size(size),
            })

        return snapshots

    def delete(self, name: str) -> bool:
        """Delete a specific snapshot. Returns True if found and deleted."""
        path = self._dir / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def cleanup(self, older_than: str) -> int:
        """Delete snapshots older than a duration string (e.g. '30d'). Returns count deleted."""
        self._ensure_dir()
        delta = _parse_duration(older_than)
        cutoff = datetime.now(timezone.utc) - delta
        count = 0

        for f in self._dir.glob("*.json"):
            try:
                # Parse timestamp from filename
                name = f.stem  # e.g. "2026-01-15-103045"
                dt = datetime.strptime(name, "%Y-%m-%d-%H%M%S").replace(
                    tzinfo=timezone.utc
                )
                if dt < cutoff:
                    f.unlink()
                    count += 1
            except (ValueError, OSError):
                continue

        return count

    def _auto_prune(self) -> None:
        """Delete oldest snapshots if count exceeds max_snapshots."""
        files = sorted(self._dir.glob("*.json"))
        while len(files) > self._max_snapshots:
            files[0].unlink()
            files.pop(0)

    def restore(self, name: str) -> ImportResult:
        """Restore from a named snapshot (with overwrite=True).

        Use name='__latest__' to restore from the most recent snapshot.
        """
        self._ensure_dir()

        if name == "__latest__":
            files = sorted(self._dir.glob("*.json"))
            if not files:
                raise FileNotFoundError("No snapshots available to restore.")
            path = str(files[-1])
        else:
            path = str(self._dir / f"{name}.json")
            if not os.path.exists(path):
                raise FileNotFoundError(f"Snapshot not found: {name}")

        return self._lore.import_data(
            file_path=path,
            overwrite=True,
            skip_embeddings=True,
        )
