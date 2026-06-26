"""Cross-product tamper-evident memory log: emit Lore memory events into
AgentLens's SHA-256 hash chain (#78).

Optional + non-blocking: OFF unless ``LORE_AGENTLENS_URL`` and
``LORE_AGENTLENS_API_KEY`` are set. Emission is fire-and-forget — it never
blocks, delays, or fails a memory write (a memory write succeeding must never
depend on AgentLens being reachable). Memory creates / supersessions / redactions
land as AgentLens ``custom`` events under a stable per-org session so they chain
together, giving an org one tamper-evident record that spans memory + the rest of
the platform.

v1 is self-reported (the ``agentId`` is sent in the event body). Verified
attribution — so these events also surface in the AgentLens cross-product
*timeline* (#98), which keys on the server-verified ``verified_agent_id`` — needs
either forwarding the inbound agent token or a service-token receiver; that is a
tracked follow-up, not this slice.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Hold strong refs to in-flight fire-and-forget tasks; without this the event
# loop may GC a bare create_task() before it runs (documented asyncio gotcha).
_inflight: set[asyncio.Task[Any]] = set()


def _config() -> Optional[tuple[str, str]]:
    url = os.environ.get("LORE_AGENTLENS_URL")
    key = os.environ.get("LORE_AGENTLENS_API_KEY")
    if url and key:
        return url.rstrip("/"), key
    return None


def agentlens_emit_enabled() -> bool:
    """True when AgentLens emission is configured (URL + API key present)."""
    return _config() is not None


async def _post(url: str, key: str, body: dict[str, Any]) -> None:
    try:
        timeout = float(os.environ.get("LORE_AGENTLENS_TIMEOUT", "3"))
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.post(
                f"{url}/api/events",
                json=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
    except Exception:  # noqa: BLE001 — best-effort telemetry must never surface
        logger.debug("AgentLens memory-event emit failed (best-effort)", exc_info=True)


def build_event_body(
    event_type: str,
    *,
    org_id: str,
    agent_id: Optional[str],
    memory_id: Optional[str],
    data: dict[str, Any],
) -> dict[str, Any]:
    """The AgentLens /api/events body for a memory event (pure; used by tests)."""
    metadata: dict[str, Any] = {"source": "lore"}
    if memory_id:
        metadata["memoryId"] = memory_id
    return {
        "events": [
            {
                "sessionId": f"lore-memory:{org_id}",
                "agentId": agent_id or "lore",
                "eventType": "custom",
                "payload": {"type": event_type, "data": data},
                "metadata": metadata,
            }
        ]
    }


def emit_memory_event(
    event_type: str,
    *,
    org_id: str,
    agent_id: Optional[str] = None,
    memory_id: Optional[str] = None,
    data: Optional[dict[str, Any]] = None,
) -> None:
    """Fire-and-forget a memory event to AgentLens. Returns immediately; no-op
    when unconfigured or when there is no running event loop. Never raises."""
    cfg = _config()
    if not cfg:
        return
    url, key = cfg
    body = build_event_body(
        event_type, org_id=org_id, agent_id=agent_id, memory_id=memory_id, data=data or {}
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (rare sync caller) — emission is best-effort, so skip
        # rather than spin up a loop and block the write.
        return
    task = loop.create_task(_post(url, key, body))
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)
