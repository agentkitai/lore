"""CI guard: migrated route files must not import asyncpg or contain raw SQL strings.

Add a route to MIGRATED_ROUTES once it has been refactored to call services
exclusively. The script fails CI if a migrated route reintroduces direct DB
access.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATED_ROUTES = {
    "src/lore/server/routes/analytics.py",
    "src/lore/server/routes/audit.py",
    "src/lore/server/routes/conversations.py",
    "src/lore/server/routes/graph/entities.py",
    "src/lore/server/routes/graph/memories.py",
    "src/lore/server/routes/graph/stats.py",
    "src/lore/server/routes/graph/topics.py",
    "src/lore/server/routes/keys.py",
    "src/lore/server/routes/lessons.py",
    "src/lore/server/routes/memories.py",
    "src/lore/server/routes/policies.py",
    "src/lore/server/routes/profiles.py",
    "src/lore/server/routes/recent.py",
    "src/lore/server/routes/recommendations.py",
    "src/lore/server/routes/retrieve.py",
    "src/lore/server/routes/review.py",
    "src/lore/server/routes/sharing.py",
    "src/lore/server/routes/slo.py",
    "src/lore/server/routes/snapshots.py",
    "src/lore/server/routes/topics.py",
    "src/lore/server/routes/workspaces.py",
}

FORBIDDEN_PATTERNS = [
    re.compile(r"^\s*import asyncpg", re.MULTILINE),
    re.compile(r"^\s*from asyncpg", re.MULTILINE),
    re.compile(r"\bget_pool\s*\(", re.MULTILINE),
    # Raw SQL heuristic: lines with SELECT/INSERT/UPDATE/DELETE inside a string literal
    re.compile(
        r'"""\s*\n?\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b',
        re.IGNORECASE | re.MULTILINE,
    ),
]

# Allowlist: known-OK references that match the patterns but are intentional.
# These are helpers that were out-of-scope for Phase 1A migration (scheduled for
# Phase 1B–1F). Each string must appear in the 60-char context window around
# the match (text[match.start()-30 : match.end()+30]).
ALLOWLIST = {
    "src/lore/server/routes/graph/memories.py": [
        "No SQL or get_pool() here.",         # module docstring asserting absence (not actual usage)
    ],
    "src/lore/server/routes/memories.py": [
        "a memory.",                          # Update/Delete docstrings (not raw SQL)
    ],
    "src/lore/server/routes/profiles.py": [
        "a profile",                          # Update/Delete docstrings (not raw SQL)
    ],
    "src/lore/server/routes/recommendations.py": [
        "recommendation config",              # Update/Delete docstrings (not raw SQL)
    ],
    "src/lore/server/routes/lessons.py": [
        "a lesson",                           # Update/Delete docstrings (not raw SQL)
    ],
    "src/lore/server/routes/policies.py": [
        "a retention policy",                 # Update/Delete docstrings (not raw SQL)
    ],
    "src/lore/server/routes/slo.py": [
        "an SLO definition",                  # Update/Delete docstrings (not raw SQL)
    ],
}


def main() -> int:
    failures: list[str] = []
    for path_str in sorted(MIGRATED_ROUTES):
        path = Path(path_str)
        if not path.exists():
            failures.append(f"{path_str}: file not found")
            continue
        text = path.read_text()
        allow = ALLOWLIST.get(path_str, [])
        for pattern in FORBIDDEN_PATTERNS:
            for match in pattern.finditer(text):
                snippet = text[max(0, match.start() - 30):match.end() + 30]
                if any(a in snippet for a in allow):
                    continue
                line_no = text[:match.start()].count("\n") + 1
                failures.append(
                    f"{path_str}:{line_no} forbidden pattern matched: {match.group(0)!r}"
                )

    if failures:
        print("Routes-no-SQL guard FAILED:")
        for f in failures:
            print(f"  {f}")
        return 1
    print(f"Routes-no-SQL guard: {len(MIGRATED_ROUTES)} files OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
