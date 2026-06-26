"""Aggregated memory provenance / lineage (#82).

Collects, for one memory, the data a UI panel (or CLI) renders: owner,
visibility, source, redaction tags, a trust signal, and the full supersession
lineage — the forward audit trail (``get_supersession_chain``) plus the source
memories it consolidated (``list_supersession_sources``). Pure aggregation over
already-fetched rows, so it's unit-testable without a server/auth.
"""

from __future__ import annotations

from typing import Any, Dict, Sequence

# Tags that mark a memory as having been redacted at write time (lore#80).
_REDACTION_TAGS = {"pii", "secret", "redacted", "masked"}


def _link(s: Any) -> Dict[str, Any]:
    return {
        "memory_id": s.memory_id,
        "superseded_by": s.superseded_by,
        "reason": s.reason,
        "ts": s.ts,
        "agent": s.agent,
    }


def build_memory_provenance(
    memory: Any,
    chain: Sequence[Any],
    sources: Sequence[Any],
) -> Dict[str, Any]:
    """Aggregate one memory's provenance + supersession lineage into a dict.

    ``chain`` = the memory's forward audit trail; ``sources`` = rows where this
    memory is the ``superseded_by`` target (what it consolidated). ``trust_signal``
    mirrors what #79 trust-aware recall keys on (owned vs anonymous).
    """
    owner = memory.user_id
    tags = list(memory.tags)
    return {
        "id": memory.id,
        "owner": owner,
        "visibility": getattr(memory, "visibility", "private") or "private",
        "source": memory.source,
        "tags": tags,
        "redaction_tags": [t for t in tags if t.startswith("redact") or t in _REDACTION_TAGS],
        "trust_signal": "anonymous" if owner is None else "owned",
        "supersession_chain": [_link(s) for s in chain],
        "supersession_sources": [_link(s) for s in sources],
        "created_at": memory.created_at,
    }
