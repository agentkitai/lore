"""Write-time cross-agent contradiction detection (#84).

After a memory is written, optionally check whether it CONTRADICTS a similar
existing memory (assert opposite facts about the same subject) and flag it for
review — LLM-scored, fire-and-forget, **OFF by default**
(``LORE_CONTRADICTION_DETECTION``). Flagged memories get a ``contradiction`` tag
plus ``meta.contradicts`` (conflicting ids, owners, cross-agent flag, reason), so
the review surface is simply ``list_memories(tags=["contradiction"])`` — no new
endpoint. Extends the AUDN reconciler (#66): reconcile folds near-duplicates;
this flags the near-duplicates that *disagree*.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, List, Optional, Tuple

from lore.persistence import MemoryPatch, RecallParams, Store

logger = logging.getLogger(__name__)

# (contradicts, confidence in [0,1], short reason)
ContradictionScorer = Callable[[str, str], Tuple[bool, float, str]]


def is_enabled() -> bool:
    return os.environ.get("LORE_CONTRADICTION_DETECTION", "").lower() in ("1", "true", "yes")


def _min_confidence() -> float:
    try:
        return min(1.0, max(0.0, float(os.environ.get("LORE_CONTRADICTION_MIN_CONFIDENCE", "0.6"))))
    except ValueError:
        return 0.6


def _concurrency() -> int:
    try:
        return max(1, int(os.environ.get("LORE_CONTRADICTION_CONCURRENCY", "4")))
    except ValueError:
        return 4


# Lazily created at runtime (not import) so it binds to the running loop; cap the
# background LLM fan-out like the sibling graph-extraction hook does.
_sem: Optional["asyncio.Semaphore"] = None


def _get_semaphore() -> "asyncio.Semaphore":
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_concurrency())
    return _sem


def _reset_semaphore() -> None:
    """Test hook: drop the cached semaphore so the next call rebinds the loop."""
    global _sem
    _sem = None


def _model() -> str:
    return os.environ.get("LORE_CONTRADICTION_MODEL") or os.environ.get(
        "LORE_ENRICHMENT_MODEL", "gpt-4o-mini"
    )


_PROMPT = (
    "Decide whether two memory statements CONTRADICT each other — assert opposite "
    "facts about the same subject (not merely different topics).\n\n"
    "A: {a}\nB: {b}\n\n"
    'Respond ONLY with JSON: {{"contradicts": true|false, "confidence": 0.0-1.0, "reason": "short"}}'
)


def _llm_scorer(a: str, b: str) -> Tuple[bool, float, str]:
    from lore.enrichment.llm import LLMClient

    client = LLMClient(model=_model())
    raw = client.complete(_PROMPT.format(a=a[:2000], b=b[:2000]), response_format={"type": "json_object"})
    data = json.loads(raw)
    return bool(data.get("contradicts")), float(data.get("confidence", 0.0)), str(data.get("reason", ""))[:300]


async def detect_and_flag(
    store: Store,
    *,
    org_id: str,
    memory_id: str,
    content: str,
    embedding: Any,
    owner_user_id: Optional[str] = None,
    scorer: Optional[ContradictionScorer] = None,
    top_k: int = 5,
    min_similarity: float = 0.75,
) -> Optional[List[str]]:
    """Find similar neighbors, score contradiction, and flag the memory if any
    disagree. Returns the conflicting ids (or None). **Never raises** — safe for
    fire-and-forget. The scorer runs in a thread (the default makes an LLM call).
    """
    try:
        scorer = scorer or _llm_scorer
        async with _get_semaphore():
            # Scope recall to what the WRITER may see (migration-026 visibility):
            # never compare against — or disclose — another principal's private
            # memory. requesting_user_id=None only for unowned/solo writes.
            neighbors = await store.recall_by_embedding(
                RecallParams(
                    org_id=org_id,
                    query_vec=list(embedding),
                    limit=top_k + 1,  # +1: the just-written memory is its own nearest neighbor
                    min_score=min_similarity,
                    scope_mode="all",
                    requesting_user_id=owner_user_id,
                )
            )
            min_conf = _min_confidence()
            conflicts: List[str] = []
            owners: dict[str, Optional[str]] = {}
            reasons: List[str] = []
            for n in neighbors:
                if n.id == memory_id:
                    continue
                contradicts, conf, reason = await asyncio.to_thread(scorer, content, n.content)
                if contradicts and conf >= min_conf:
                    conflicts.append(n.id)
                    owners[n.id] = n.user_id
                    if reason:
                        reasons.append(reason)
        if not conflicts:
            return None

        existing = await store.get_memory(org_id, memory_id)
        if existing is None:
            return None
        tags = tuple(sorted(set(existing.tags) | {"contradiction"}))
        meta = {
            **dict(existing.meta),
            "contradicts": conflicts,
            "contradiction_owners": owners,
            # A different, non-null owner — an unowned neighbor isn't "another agent".
            "cross_agent": any(
                owners.get(i) is not None and owners.get(i) != owner_user_id for i in conflicts
            ),
            "contradiction_reason": "; ".join(reasons)[:500],
        }
        await store.update_memory(org_id, memory_id, MemoryPatch(tags=tags, meta=meta))
        logger.info(
            "contradiction flagged org=%s memory=%s conflicts=%d", org_id, memory_id, len(conflicts)
        )
        return conflicts
    except Exception:  # fire-and-forget: a flagging failure must never break a write
        logger.warning(
            "contradiction detection failed org=%s memory=%s", org_id, memory_id, exc_info=True
        )
        return None
