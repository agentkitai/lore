"""CI guard: every Postgres migration must have a SQLite sibling.

Required by the SQLite solo-mode design (decision #7): a PR that adds
`migrations/NNN_*.sql` without a matching `migrations_sqlite/NNN_*.sql`
fails CI. The matching is by leading three-digit version number; the
descriptive suffix may differ between the two trees.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PG_DIR = ROOT / "migrations"
SQLITE_DIR = ROOT / "migrations_sqlite"

VERSION_RE = re.compile(r"^(\d{3})_.+\.sql$")


def _versions(directory: Path) -> dict[str, str]:
    """Return {version: filename} for migration files in a directory."""
    out: dict[str, str] = {}
    if not directory.exists():
        return out
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix != ".sql":
            continue
        m = VERSION_RE.match(path.name)
        if not m:
            continue
        version = m.group(1)
        if version in out:
            print(
                f"ERROR: duplicate migration version {version} in {directory}: "
                f"{out[version]} and {path.name}"
            )
            return {}
        out[version] = path.name
    return out


def main() -> int:
    pg = _versions(PG_DIR)
    sl = _versions(SQLITE_DIR)

    failures: list[str] = []
    for version, name in pg.items():
        if version not in sl:
            failures.append(
                f"  Postgres migration {name} has no SQLite sibling "
                f"(expected migrations_sqlite/{version}_*.sql)"
            )
    for version, name in sl.items():
        if version not in pg:
            failures.append(
                f"  SQLite migration {name} has no Postgres sibling "
                f"(expected migrations/{version}_*.sql)"
            )

    if failures:
        print("Migrations-parity guard FAILED:")
        for f in failures:
            print(f)
        return 1

    print(f"Migrations-parity guard: {len(pg)} versions OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
