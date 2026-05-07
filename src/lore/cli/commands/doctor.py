"""``lore doctor`` — diagnose key/DB drift and self-heal.

Detects the most common solo-mode failure: ``~/.lore/key.txt`` and
``~/.lore/lore.db`` get out of sync (a key.txt rewrite, a DB rebuild,
etc.). Diagnoses what's wrong; ``--fix`` applies the safe repair.

Repairs are conservative:
  * If key.txt has a value whose hash isn't in the DB → INSERT that hash
    (we can re-link the existing file to the existing DB without
    regenerating).
  * If key.txt is missing but the DB has a key → can't recover (we only
    have the hash, not the raw key). Suggest a full reset.
  * If both are empty → suggest ``lore serve`` (will bootstrap fresh).

Read-only by default. ``--fix`` applies; ``--json`` prints structured
state for scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_KEY_PATH = Path.home() / ".lore" / "key.txt"
DEFAULT_DB_PATH = Path.home() / ".lore" / "lore.db"
DEFAULT_ENV_PATH = Path.cwd() / ".env"


def _read_key_file(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        content = path.read_text().strip()
        return content or None
    except OSError:
        return None


def _read_env_key(path: Path) -> Optional[str]:
    """Parse LORE_API_KEY=... from a .env-style file. Best-effort."""
    if not path.exists():
        return None
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith("LORE_API_KEY="):
                value = line[len("LORE_API_KEY="):].strip()
                # Tolerate quotes
                if value and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                return value or None
    except OSError:
        pass
    return None


async def _list_db_keys(db_path: Path) -> Optional[list]:
    """Return list of (key_hash, key_prefix) tuples from the api_keys table.

    Returns None if the DB doesn't exist or is unreadable.
    """
    if not db_path.exists():
        return None
    try:
        import aiosqlite
    except ImportError:
        return None
    try:
        async with aiosqlite.connect(str(db_path)) as conn:
            async with conn.execute(
                "SELECT key_hash, key_prefix FROM api_keys WHERE revoked_at IS NULL"
            ) as cur:
                rows = await cur.fetchall()
        return [(row[0], row[1]) for row in rows]
    except Exception:
        return None


async def _insert_key_into_db(db_path: Path, key_hash: str, key_prefix: str) -> bool:
    """Insert a key_hash into api_keys for the solo org. Returns True on success."""
    try:
        import aiosqlite
    except ImportError:
        return False
    try:
        from ulid import ULID
        key_id = f"key_{ULID()}"
    except ImportError:
        # Fallback: generate a UUID-like id
        import secrets
        key_id = f"key_{secrets.token_hex(16)}"
    try:
        async with aiosqlite.connect(str(db_path)) as conn:
            await conn.execute(
                "INSERT INTO api_keys (id, org_id, name, key_hash, key_prefix, "
                "is_root, workspace_id) VALUES (?, ?, ?, ?, ?, 1, ?)",
                (key_id, "solo", "solo-root-recovered", key_hash,
                 key_prefix, "solo"),
            )
            await conn.commit()
        return True
    except Exception:
        return False


def _diagnose(
    *,
    key_file: Optional[str],
    db_keys: Optional[list],
    env_key: Optional[str],
) -> Dict[str, Any]:
    """Return a structured state dict describing the install."""
    state: Dict[str, Any] = {
        "key_file_present": key_file is not None,
        "key_file_prefix": (key_file[:12] if key_file else None),
        "db_present": db_keys is not None,
        "db_key_count": (len(db_keys) if db_keys else 0),
        "db_key_prefixes": [p for _, p in (db_keys or [])],
        "env_key_present": env_key is not None,
        "env_key_prefix": (env_key[:12] if env_key else None),
        "issues": [],
        "fixable": False,
    }

    if key_file is None and (db_keys is None or len(db_keys) == 0):
        state["issues"].append(
            "no key file and no DB keys — run `lore serve` to bootstrap"
        )
        return state

    if key_file is None and db_keys:
        state["issues"].append(
            "key.txt missing but DB has keys; can't recover (only hashes "
            "stored). Use `lore keys create` to mint a new one."
        )
        return state

    if key_file and (db_keys is None or len(db_keys) == 0):
        state["issues"].append(
            "key.txt exists but DB is empty — `lore doctor --fix` will "
            "import the file's key into the DB."
        )
        state["fixable"] = True
        return state

    # Both exist. Check hash match.
    assert key_file is not None and db_keys is not None
    file_hash = hashlib.sha256(key_file.encode()).hexdigest()
    file_in_db = any(h == file_hash for h, _ in db_keys)

    if not file_in_db:
        state["issues"].append(
            "key.txt's hash isn't in the DB — drift detected. "
            "`lore doctor --fix` will import the file's key into the DB."
        )
        state["fixable"] = True

    # Check .env drift
    if env_key and key_file and env_key != key_file:
        state["issues"].append(
            f".env's LORE_API_KEY ({env_key[:12]}...) doesn't match "
            f"key.txt ({key_file[:12]}...). The .env value is what "
            "applications use."
        )
        # We could fix this but it's safer to leave .env alone — the user
        # may have intentionally pointed .env at a different server.

    return state


def cmd_doctor(args: argparse.Namespace) -> None:
    key_path = Path(args.key_path or DEFAULT_KEY_PATH).expanduser()
    db_path = Path(args.db_path or DEFAULT_DB_PATH).expanduser()
    env_path = Path(args.env_path or DEFAULT_ENV_PATH).expanduser()

    key_file = _read_key_file(key_path)
    db_keys = asyncio.run(_list_db_keys(db_path))
    env_key = _read_env_key(env_path)

    state = _diagnose(key_file=key_file, db_keys=db_keys, env_key=env_key)

    if args.json:
        print(json.dumps(state, indent=2))
        return

    print(f"key.txt:  {key_path}")
    if key_file:
        print(f"  ✓ present (prefix {key_file[:12]}...)")
    else:
        print("  ✗ missing or empty")

    print(f"DB:       {db_path}")
    if db_keys is None:
        print("  ✗ missing or unreadable")
    elif not db_keys:
        print("  ✓ exists, but no active root keys")
    else:
        for h, p in db_keys:
            print(f"  ✓ key {p}... (hash {h[:8]}...)")

    print(f".env:     {env_path}")
    if env_key:
        print(f"  ✓ LORE_API_KEY = {env_key[:12]}...")
    else:
        print("  ✗ no LORE_API_KEY set")

    if not state["issues"]:
        print("\n✅ No issues detected.")
        return

    print("\n⚠️  Issues detected:")
    for issue in state["issues"]:
        print(f"  • {issue}")

    if args.fix and state["fixable"] and key_file and db_path.exists():
        print("\n--- applying fix ---")
        prefix = key_file[:12]
        h = hashlib.sha256(key_file.encode()).hexdigest()
        ok = asyncio.run(_insert_key_into_db(db_path, h, prefix))
        if ok:
            print("✅ Imported key.txt's key into the DB.")
            print("   Restart `lore serve` if it's currently running.")
        else:
            print("❌ Fix failed. Try `pkill -f 'lore serve'` first, then "
                  "re-run `lore doctor --fix`.")
    elif state["fixable"]:
        print("\nRun with --fix to repair.")


def add_doctor_subparser(sub: Any) -> None:
    p = sub.add_parser(
        "doctor",
        help="Diagnose ~/.lore/key.txt vs ~/.lore/lore.db drift; --fix to repair.",
    )
    p.add_argument("--fix", action="store_true",
                   help="Apply safe repairs (import key.txt's key into the DB).")
    p.add_argument("--json", action="store_true",
                   help="Print structured diagnosis (no formatted output).")
    p.add_argument("--key-path", help="Override path to key.txt.")
    p.add_argument("--db-path", help="Override path to lore.db.")
    p.add_argument("--env-path", help="Override path to .env.")
