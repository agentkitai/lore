"""Snapshots service — session-snapshot creation as tagged memories.

Snapshots aren't stored in a separate table — they're memories with
meta.type='session_snapshot' and tags=['session_snapshot', session_id, *user_tags].

Bug fix vs. the pre-1E route: the old INSERT referenced non-existent
`tier` and `type` columns directly on the `memories` table. The reader
path (`_fetch_session_snapshots`) already queries `meta->>'type'`, so
this service moves both keys into `meta` to match.
"""

from __future__ import annotations

import uuid
from typing import Optional, Sequence

from lore.persistence import NewMemory, Store, StoredMemory


def _make_session_id() -> str:
    return uuid.uuid4().hex[:12]


async def create_snapshot(
    store: Store,
    *,
    org_id: str,
    content: str,
    title: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    project: Optional[str] = None,
    user_id: Optional[str] = None,
) -> StoredMemory:
    """Create a session snapshot stored as a tagged memory."""
    # Write-side redaction: snapshots capture raw session state, so scrub
    # secrets/PII before persistence (and derive the title from the scrubbed
    # content so it can't leak either).
    from lore.redact.write import get_write_redactor, redact_for_write

    content, title, redaction_meta = redact_for_write(get_write_redactor(), content, title)
    sid = session_id or _make_session_id()
    snap_title = title or content[:80].strip()
    all_tags = ("session_snapshot", sid, *(tags or ()))
    meta = {
        "session_id": sid,
        "title": snap_title,
        "extraction_method": "raw",
        "type": "session_snapshot",
        "tier": "long",
        **redaction_meta,
    }
    nm = NewMemory(
        org_id=org_id,
        content=content,
        embedding=[0.0] * 384,    # snapshots aren't recall targets; placeholder zero-vector
        tags=all_tags,
        project=project,
        meta=meta,
        user_id=user_id,    # own the snapshot (#71): session state is the creator's, private by default
    )
    return await store.insert_memory(nm)
