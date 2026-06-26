"""Forget-with-proof: GDPR erasure + a signed deletion certificate (#81).

Deletes a subject's memories (every row a principal owns) or an explicit set, and
returns a tamper-evident **deletion certificate** — what was deleted, when, for
whom — content-hashed and (when ``LORE_DELETION_SIGNING_KEY`` is set) HMAC-signed,
so the erasure is provable to an auditor/data subject. Competitors offer deletion
but not a signed certificate.

**Erasure scope (be precise — the cert must not over-promise):** deleting a
``memories`` row cascades (FK) to its vector, ``entity_mentions`` and
``memory_supersessions``. It does NOT yet scrub derived data that lacks a FK to
``memories`` — knowledge-graph ``relationships`` (subject/predicate/object facts),
``recommendation_feedback``, the ``retrieval_analytics``/``conversation_jobs``
JSON id lists, or any AgentLens cross-product event already emitted. Full
graph/analytics cascade is a tracked follow-up; until then the cert attests
exactly the ``memories`` rows it lists, no more.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from lore.persistence import MemoryFilter, Store

logger = logging.getLogger(__name__)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def build_deletion_certificate(
    *,
    org_id: str,
    deleted_ids: Sequence[str],
    requested_count: int,
    subject_user_id: Optional[str] = None,
    deleted_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """A signed deletion certificate. Pure (no I/O) so it's unit-testable.

    ``contentHash`` and the optional HMAC ``signature`` both cover the same
    canonical core body, so either modification is detectable. Signing key from
    ``LORE_DELETION_SIGNING_KEY``; absent → ``signature: null`` (the content hash
    is still tamper-evident).
    """
    core: Dict[str, Any] = {
        "kind": "lore.deletion-certificate/v1",
        "orgId": org_id,
        "subject": {"userId": subject_user_id},
        "deletedMemoryIds": sorted(deleted_ids),
        "deletedCount": len(deleted_ids),
        "requestedCount": requested_count,
        "deletedAt": (deleted_at or datetime.now(timezone.utc)).isoformat(),
    }
    canon = _canonical(core)
    core["contentHash"] = "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()
    key = os.environ.get("LORE_DELETION_SIGNING_KEY")
    if key:
        value = hmac.new(key.encode("utf-8"), canon.encode("utf-8"), hashlib.sha256).hexdigest()
        core["signature"] = {"type": "hmac", "alg": "sha256", "value": value}
    else:
        core["signature"] = None
    return core


def verify_deletion_certificate(cert: Dict[str, Any], *, signing_key: Optional[str] = None) -> Dict[str, Any]:
    """Verify a deletion certificate's content hash (and HMAC, when a key is
    given). Strips ``contentHash``/``signature`` before re-canonicalizing — the
    same body the builder hashed. Returns ``{"valid": bool, "reason"?: str}``."""
    core = {k: v for k, v in cert.items() if k not in ("contentHash", "signature")}
    canon = _canonical(core)
    if cert.get("contentHash") != "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest():
        return {"valid": False, "reason": "content hash mismatch"}
    if signing_key:
        sig = cert.get("signature")
        if not isinstance(sig, dict) or sig.get("type") != "hmac":
            return {"valid": False, "reason": "missing or unsupported signature"}
        expected = hmac.new(signing_key.encode("utf-8"), canon.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, str(sig.get("value", ""))):
            return {"valid": False, "reason": "signature mismatch"}
    return {"valid": True}


async def forget_with_proof(
    store: Store,
    *,
    org_id: str,
    user_id: Optional[str] = None,
    memory_ids: Optional[Sequence[str]] = None,
    requesting_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Erase memories and return a signed deletion certificate.

    Provide EXACTLY ONE of:
      - ``user_id``: subject erasure — every memory OWNED by the subject.
      - ``memory_ids``: an explicit set (admin/operator escape hatch). Deletes
        the given ids within ``org_id`` with NO per-owner check (org-bounded);
        the cert records no subject. Treat as an admin-only path.

    The cert covers the rows ACTUALLY removed (delete returned True). A
    per-row delete failure is logged and excluded — the op is best-effort /
    non-transactional, and ``deletedCount < requestedCount`` signals an
    incomplete erasure honestly.
    """
    if (user_id is None) == (memory_ids is None):
        raise ValueError("forget_with_proof requires EXACTLY ONE of user_id or memory_ids")

    if memory_ids is not None:
        targets: List[str] = list(memory_ids)
    else:
        # List the requester's visible set, then keep only rows OWNED by the
        # subject (user_id match) — that's the subject's personal data to erase.
        mems = await store.list_memories(
            MemoryFilter(org_id=org_id, requesting_user_id=user_id, include_expired=True)
        )
        targets = [m.id for m in mems if m.user_id == user_id]

    deleted: List[str] = []
    for mid in targets:
        try:
            if await store.delete_memory(org_id, mid, requesting_user_id=requesting_user_id):
                deleted.append(mid)
        except Exception:
            # Best-effort: a failed row is reported as not-deleted (the cert stays
            # honest) rather than aborting and losing proof of what was erased.
            logger.warning("forget_with_proof: delete failed for %s (org %s)", mid, org_id, exc_info=True)

    return build_deletion_certificate(
        org_id=org_id,
        deleted_ids=deleted,
        requested_count=len(targets),
        subject_user_id=user_id,
    )
