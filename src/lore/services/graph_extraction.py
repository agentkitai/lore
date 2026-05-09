"""Graph extraction service — populates entities / mentions / relationships
from a memory's content + context via a `claude -p` subagent.

Design: docs/superpowers/specs/2026-05-08-lore-graph-population-design.md.

The service spawns a one-shot `claude -p` subprocess with a deterministic
extraction prompt, parses the JSON from the final assistant message, and
persists the result via the Store's GraphOps slice.

Why subprocess and not the Anthropic SDK directly:
  * Reuses the dream / capture infrastructure (PRs #48, #49 already
    established the spawn-flag pattern).
  * Authentication piggybacks on the user's Claude Code login — no new
    secret to manage.
  * No new dependency on the ``anthropic`` Python SDK in lore-sdk's
    core deps.

The trade-off is ~500ms-1s of subprocess-spawn overhead per memory. The
``LORE_GRAPH_EXTRACTION_CONCURRENCY`` semaphore caps parallelism so a
50-memory dream-finalize burst doesn't spawn 50 subprocesses at once.

Failure modes (timeout / parse error / claude not on PATH / non-2xx
exit) all log and return an ``ExtractionResult`` with ``error`` set —
no exception bubbles to the caller. Failed extractions can be retried
via ``POST /v1/graph/backfill``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from lore.persistence import (
    NewEntity,
    NewMention,
    NewRelationship,
    Store,
)
from lore.subagent_config import subagent_config

logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────


_VALID_ENTITY_TYPES = {
    "person",
    "project",
    "technology",
    "concept",
    "organization",
    "location",
    "other",
}

# Lazily resolve env-driven knobs so tests / runtime can monkeypatch
# the env before the first call without juggling import order.
_DEFAULT_CONCURRENCY = 2
_DEFAULT_TIMEOUT_S = 30.0


def _concurrency() -> int:
    raw = os.environ.get("LORE_GRAPH_EXTRACTION_CONCURRENCY")
    if not raw:
        return _DEFAULT_CONCURRENCY
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_CONCURRENCY
    return max(1, n)


def _timeout_s() -> float:
    raw = os.environ.get("LORE_GRAPH_EXTRACTION_TIMEOUT")
    if not raw:
        return _DEFAULT_TIMEOUT_S
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_TIMEOUT_S


# Module-level semaphore. Built lazily on first acquire so the env var
# is read at runtime, not import time. Tests reset via ``_reset_semaphore``.
_sem: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_concurrency())
    return _sem


def _reset_semaphore() -> None:
    """Test-only: drop the cached semaphore so the next call re-reads the env."""
    global _sem
    _sem = None


def is_enabled() -> bool:
    """Feature flag.

    Auto-on iff ``claude`` is on PATH (matching the dream / capture probe).
    Explicitly settable via ``LORE_GRAPH_EXTRACTION_ENABLED`` (``true`` /
    ``false`` / ``1`` / ``0``).
    """
    raw = os.environ.get("LORE_GRAPH_EXTRACTION_ENABLED")
    if raw is not None:
        return raw.lower() in ("1", "true", "yes")
    return shutil.which("claude") is not None


# ── Prompt + response parsing ──────────────────────────────────────


_PROMPT_TEMPLATE = """You are an entity-extraction worker. Read the memory below and return a single JSON object. Do not call any tools. Do not include any text outside the JSON.

Memory content:
{content}
{context_block}

Schema:

  {{
    "entities": [
      {{"name": "<canonical name>",
        "type": "<one of: person, project, technology, concept, organization, location, other>",
        "description": "<one line>",
        "aliases": ["<other ways this is referenced>"],
        "confidence": 0.0-1.0}}
    ],
    "relationships": [
      {{"subject": "<name from entities[]>",
        "predicate": "<verb-phrase, kebab-case>",
        "object": "<name from entities[]>",
        "confidence": 0.0-1.0}}
    ]
  }}

Only extract entities and relationships explicitly stated. Do not infer.
Do not extract pronouns or indefinite references.
Empty arrays are fine. Return JSON, nothing else.
"""


def _build_extraction_prompt(*, content: str, context: Optional[str]) -> str:
    """Render the deterministic extraction prompt."""
    context_block = f"\nMemory context:\n{context}\n" if context else ""
    return _PROMPT_TEMPLATE.format(content=content, context_block=context_block)


# Match the first JSON object in a string. Tolerant of leading/trailing
# whitespace and (in practice) Claude's occasional ```json fences.
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*({.*?})\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> Optional[dict]:
    """Pull a JSON object out of a free-form string. Returns None on failure."""
    if not text:
        return None
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        candidate = fence.group(1)
    else:
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            return None
        candidate = match.group(0)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _parse_extraction_response(stdout: str) -> Optional[dict]:
    """Parse `claude -p --output-format stream-json --verbose` output and
    return the JSON object the worker emitted in its final assistant message.

    Stream-json emits one JSON event per line. The shape varies by Claude
    Code version; we walk every line, capture the latest assistant ``text``
    content, then try to extract a JSON object from it. Robust to harmless
    mid-stream messages (system, tool_use, hook events, partial chunks).
    """
    if not stdout:
        return None
    last_text: Optional[str] = None
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "assistant":
            continue
        message = event.get("message") or {}
        content = message.get("content") or []
        # ``content`` is a list of {type, text|...} dicts in Anthropic's shape.
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                last_text = part["text"]
    if last_text is None:
        # Fall back: maybe the runtime emitted ``output_format=text`` for
        # some reason and the whole stdout is the JSON.
        return _extract_json(stdout)
    return _extract_json(last_text)


# ── Spawn ──────────────────────────────────────────────────────────


SpawnFn = Callable[[str], "subprocess.Popen[bytes]"]


def _spawn_claude(prompt: str) -> "subprocess.Popen[bytes]":
    """Default subprocess factory: ``claude -p`` with the standard flags.

    Mirrors the dream/capture spawn pattern (post PR #48, #49):

      * ``--output-format stream-json --verbose`` — required by Claude
        Code 2.1.x when ``--print`` is set.
      * ``--permission-mode default`` — the extractor only emits text
        JSON, no MCP tool calls, so we don't need bypassPermissions.

    Caller is responsible for ``.wait()`` and reading stdout.
    """
    cfg = subagent_config(role="graph", with_lore_mcp=False)
    return subprocess.Popen(  # noqa: S603 — internal prompt
        [
            "claude", "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "default",
            *cfg.claude_flags(),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # Recursion guard — see lore.subagent_config docstring.
        env={**os.environ, **cfg.env_overrides()},
    )


# ── Result dataclass ───────────────────────────────────────────────


@dataclass(slots=True)
class ExtractionResult:
    memory_id: str
    entities_inserted: int = 0
    entities_reused: int = 0
    mentions_inserted: int = 0
    relationships_inserted: int = 0
    error: Optional[str] = None
    extracted: dict = field(default_factory=dict)  # parsed LLM JSON, for tests / debug


# ── Public entry point ─────────────────────────────────────────────


async def extract_and_persist(
    store: Store,
    *,
    org_id: str,
    memory_id: str,
    content: str,
    context: Optional[str],
    spawn_fn: Optional[SpawnFn] = None,
    timeout: Optional[float] = None,
) -> ExtractionResult:
    """Run extraction on a single memory and persist the result.

    Idempotent: any pre-existing mentions / relationships originating from
    this memory are deleted before insertion. Entities are kept (other
    memories may reference them); only this memory's edges are rewritten.

    Failure modes are swallowed — errors land on the returned
    ``ExtractionResult.error`` field. The backfill endpoint is the
    recovery mechanism.

    ``spawn_fn`` is the testing seam: pass a fake subprocess factory in
    tests; production code uses ``_spawn_claude``. ``timeout`` overrides
    the env-driven default.
    """
    result = ExtractionResult(memory_id=memory_id)
    spawn = spawn_fn or _spawn_claude
    deadline = timeout if timeout is not None else _timeout_s()

    if spawn_fn is None and shutil.which("claude") is None:
        result.error = "claude not on PATH"
        return result

    prompt = _build_extraction_prompt(content=content, context=context)

    sem = _get_semaphore()
    async with sem:
        try:
            proc = spawn(prompt)
        except OSError as e:
            result.error = f"spawn failed: {e}"
            return result

        # Wait + read in a thread so we don't block the event loop.
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                asyncio.to_thread(proc.communicate),
                timeout=deadline,
            )
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
            result.error = f"subprocess timeout after {deadline}s"
            return result
        except Exception as e:  # pragma: no cover — defensive
            result.error = f"subprocess error: {e}"
            return result

        if proc.returncode and proc.returncode != 0:
            tail = (stdout_bytes or b"").decode("utf-8", errors="replace")[-500:]
            result.error = f"subprocess exit {proc.returncode}: {tail}"
            return result

    payload = _parse_extraction_response((stdout_bytes or b"").decode("utf-8", errors="replace"))
    if payload is None:
        result.error = "parse failed (no JSON in assistant output)"
        return result

    result.extracted = payload
    await _persist(store, org_id=org_id, memory_id=memory_id, payload=payload, result=result)
    return result


# ── Persistence ────────────────────────────────────────────────────


async def _persist(
    store: Store,
    *,
    org_id: str,
    memory_id: str,
    payload: dict,
    result: ExtractionResult,
) -> None:
    """Upsert entities, replace mentions + relationships from the LLM payload."""
    raw_entities = payload.get("entities") or []
    raw_relationships = payload.get("relationships") or []
    if not isinstance(raw_entities, list):
        raw_entities = []
    if not isinstance(raw_relationships, list):
        raw_relationships = []

    # Pass 1: resolve every extracted entity to an entity_id, building a
    # name → id map for relationship resolution.
    name_to_id: dict[str, str] = {}
    name_to_confidence: dict[str, float] = {}
    now = datetime.now(timezone.utc)

    for raw in raw_entities:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()

        entity_type = raw.get("type") or "other"
        if entity_type not in _VALID_ENTITY_TYPES:
            entity_type = "other"

        description = raw.get("description")
        if not isinstance(description, str):
            description = None

        aliases_raw = raw.get("aliases") or []
        aliases = tuple(
            a.strip() for a in aliases_raw
            if isinstance(a, str) and a.strip() and a.strip().lower() != name.lower()
        )

        confidence = raw.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        confidence = max(0.0, min(1.0, float(confidence)))
        name_to_confidence[name] = confidence

        existing = await store.find_entity_by_name_or_alias(name)
        if existing is not None:
            name_to_id[name] = existing.id
            result.entities_reused += 1
            continue

        new = await store.upsert_entity(
            NewEntity(
                name=name,
                entity_type=entity_type,
                aliases=aliases,
                description=description,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        name_to_id[name] = new.id
        result.entities_inserted += 1

    # Pass 2: build mentions for every resolved entity.
    mentions: list[NewMention] = []
    for name, entity_id in name_to_id.items():
        mentions.append(
            NewMention(
                entity_id=entity_id,
                memory_id=memory_id,
                mention_type="extracted",
                confidence=name_to_confidence.get(name, 0.5),
            )
        )
    result.mentions_inserted = await store.replace_memory_mentions(memory_id, mentions)

    # Pass 3: resolve and persist relationships. Skip rows whose subject
    # or object names didn't make it into name_to_id (the LLM occasionally
    # emits a relationship referencing an entity it forgot to declare).
    relationships: list[NewRelationship] = []
    for raw in raw_relationships:
        if not isinstance(raw, dict):
            continue
        subject = raw.get("subject")
        predicate = raw.get("predicate")
        obj = raw.get("object")
        if not all(isinstance(x, str) and x.strip() for x in (subject, predicate, obj)):
            continue
        s_id = name_to_id.get(subject.strip())
        o_id = name_to_id.get(obj.strip())
        if s_id is None or o_id is None:
            continue
        weight = raw.get("confidence")
        if not isinstance(weight, (int, float)):
            weight = 0.5
        weight = max(0.0, min(1.0, float(weight)))

        relationships.append(
            NewRelationship(
                source_entity_id=s_id,
                target_entity_id=o_id,
                rel_type=predicate.strip(),
                weight=weight,
                source_memory_id=memory_id,
                valid_from=now,
            )
        )
    result.relationships_inserted = await store.replace_memory_relationships(
        memory_id, relationships,
    )

    # ``org_id`` is currently unused — the entities / mentions /
    # relationships schema is global per migration 007. Threading it
    # through the call signature anyway means callers don't have to
    # change when we add an org-scoped graph migration later.
    _ = org_id
