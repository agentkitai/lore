"""``lore dream`` — Phase 6E memory consolidation command.

The full design lives in ``docs/superpowers/specs/2026-05-07-lore-dreaming-design.md``.
This module is the user-facing entry point for the 4-phase pipeline:

  1. **Orient**       — gather stats from the Store (memory counts, types,
                        top entities, recent activity).
  2. **Gather Signal**— grep recent Claude Code transcripts for
                        corrections, "actually...", explicit ``save_snapshot``
                        / ``remember`` calls.
  3. **Consolidate**  — subagent merges duplicates, resolves contradictions,
                        promotes observations.
  4. **Prune & Index**— subagent forgets stale low-importance entries.

Phases 3 + 4 run inside a ``claude -p`` subagent that has Lore's MCP
tools. This module builds the prompt with Phase 1 + 2 inputs embedded,
spawns the subagent, and persists the dream-run record.

Concurrency: a non-blocking ``flock`` on ``~/.lore/dreams/lock`` ensures
only one dream runs at a time. ``--review`` mode tells the subagent to
emit a markdown report instead of mutating the DB.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as _dt
import errno
import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_ORG_ID = "solo"
DEFAULT_DATABASE_URL = "sqlite:///~/.lore/lore.db"

# Phase 2 transcript-grep configuration.
TRANSCRIPT_MAX_DAYS = 30
TRANSCRIPT_MAX_SESSIONS = 50
TRANSCRIPT_MAX_LINES_PER_FILE = 5000
TRANSCRIPT_SIGNAL_PATTERNS = (
    r"\bactually\b",
    r"\bno wait\b",
    r"\bi prefer\b",
    r"\bi want\b",
    r"\binstead of\b",
    r"\bdon'?t\b",
    r"mcp__lore__save_snapshot",
    r"mcp__lore__remember\b",
)

# Subagent phase markers — emitted by the worker and parsed by this module.
PHASE_MARKERS = (
    "PHASE_1_ORIENT_COMPLETE",
    "PHASE_2_SIGNAL_COMPLETE",
    "PHASE_3_CONSOLIDATE_COMPLETE",
    "PHASE_4_PRUNE_COMPLETE",
)
PHASE3_RE = re.compile(r"PHASE_3_CONSOLIDATE_COMPLETE:\s*(\d+)\s+(\d+)")
PHASE4_RE = re.compile(r"PHASE_4_PRUNE_COMPLETE:\s*(\d+)")
RUN_ID_RE = re.compile(r"^RUN_ID:\s*(\S+)\s*$", re.MULTILINE)


# ── Path helpers ──────────────────────────────────────────────────


def _dreams_root() -> Path:
    return Path.home() / ".lore" / "dreams"


def _dream_dir(run_id: str) -> Path:
    return _dreams_root() / re.sub(r"[^A-Za-z0-9_.\-]", "_", run_id)[:64]


def _dreams_lock_path() -> Path:
    return _dreams_root() / "lock"


def _extract_log_path(run_id: str) -> Path:
    return _dream_dir(run_id) / "extract.log"


def _errors_log_path(run_id: str) -> Path:
    return _dream_dir(run_id) / "errors.log"


def _proposed_md_path(run_id: str) -> Path:
    return _dream_dir(run_id) / "proposed.md"


# ── Concurrency ────────────────────────────────────────────────────


@contextlib.contextmanager
def _dreams_lock():
    """Non-blocking ``flock`` on ``~/.lore/dreams/lock``.

    Yields True if acquired, False if another dream is in flight.
    Either way the file descriptor is released on exit.
    """
    lock_path = _dreams_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        yield False
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                yield False
                return
            yield False
            return
        try:
            yield True
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


# ── Phase 1: Orient — assemble stats from Store ────────────────────


async def _gather_phase1_stats(store, org_id: str) -> dict[str, Any]:
    """Phase 1: stats assembled from existing Store endpoints.

    Returns: {total_memories, by_type, top_entities, recent_24h_count,
              recent_7d_count}.

    Implementation note: we lean on ``get_graph_stats`` for the bulk of
    the numbers (avoids reimplementing aggregation in the service
    layer); fall back to Store.list_memories for the per-type breakdown
    when get_graph_stats is unavailable for a backend.
    """
    from lore.persistence.types import MemoryFilter

    out: dict[str, Any] = {
        "total_memories": 0,
        "by_type": {},
        "top_entities": [],
        "recent_24h_count": 0,
        "recent_7d_count": 0,
    }

    # Recent memories (last 7d) for type breakdown.
    now = datetime.now(timezone.utc)
    since_7d = now - timedelta(days=7)
    since_24h = now - timedelta(hours=24)
    try:
        recent = await store.list_memories(
            MemoryFilter(org_id=org_id, since=since_7d, limit=500),
        )
        out["recent_7d_count"] = len(recent)
        out["recent_24h_count"] = sum(
            1 for m in recent
            if _ensure_aware(m.created_at) >= since_24h
        )
        # Per-type breakdown from meta.type
        ctr: Counter[str] = Counter()
        for m in recent:
            t = (m.meta or {}).get("type") if isinstance(m.meta, dict) else None
            ctr[t or "untyped"] += 1
        out["by_type"] = dict(ctr.most_common(10))
    except Exception as exc:  # noqa: BLE001 — fail-soft Phase 1 gathering
        out["by_type"] = {}
        out["_phase1_recent_error"] = repr(exc)

    # Total + top entities via get_graph_stats (already aggregated).
    try:
        gs = await store.get_graph_stats()
        out["total_memories"] = int(gs.total_memories)
        out["top_entities"] = [
            {"name": e.get("name"), "mentions": e.get("mentions") or e.get("mention_count")}
            for e in (gs.top_entities or [])[:20]
        ]
    except Exception as exc:  # noqa: BLE001
        out["_phase1_graph_error"] = repr(exc)

    return out


# ── Phase 2: Gather Signal — grep transcripts ──────────────────────


def _claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def _list_recent_transcripts(
    *,
    max_days: int = TRANSCRIPT_MAX_DAYS,
    max_sessions: int = TRANSCRIPT_MAX_SESSIONS,
) -> list[Path]:
    """Find recent ``*.jsonl`` transcripts under ``~/.claude/projects/``.

    Caps at ``min(max_days, max_sessions)``. Returns the newest-first
    slice. Missing root → empty list.
    """
    root = _claude_projects_dir()
    if not root.exists():
        return []
    cutoff = _dt.datetime.now().timestamp() - max_days * 86400
    candidates: list[tuple[float, Path]] = []
    for p in root.rglob("*.jsonl"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        candidates.append((mtime, p))
    candidates.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in candidates[:max_sessions]]


def _grep_transcript_signals(
    paths: list[Path],
    *,
    patterns: tuple[str, ...] = TRANSCRIPT_SIGNAL_PATTERNS,
    max_lines_per_file: int = TRANSCRIPT_MAX_LINES_PER_FILE,
    max_total_hits: int = 200,
) -> list[dict[str, Any]]:
    """Phase 2: scan recent transcripts for signal patterns.

    Reads only ``[user]`` content blocks (skips assistant turns to keep
    signal density high and avoid model confidence noise). Caps each
    file at ``max_lines_per_file`` and the total hits at
    ``max_total_hits`` to bound prompt size.

    Returns a list of ``{file, pattern, snippet}`` dicts.
    """
    compiled = [re.compile(pat, re.IGNORECASE) for pat in patterns]
    out: list[dict[str, Any]] = []
    for path in paths:
        if len(out) >= max_total_hits:
            break
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= max_lines_per_file:
                        break
                    if len(out) >= max_total_hits:
                        break
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") != "user":
                        continue
                    msg = obj.get("message") or {}
                    content = msg.get("content")
                    text = _extract_user_text(content)
                    if not text:
                        continue
                    for cre, pat in zip(compiled, patterns):
                        if cre.search(text):
                            snippet = text[:180]
                            out.append({
                                "file": path.name,
                                "pattern": pat,
                                "snippet": snippet,
                            })
                            if len(out) >= max_total_hits:
                                break
        except OSError:
            continue
    return out


def _extract_user_text(content: Any) -> Optional[str]:
    """Pull plain text from a Claude Code transcript user content block."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict):
                t = p.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return " ".join(parts) if parts else None
    return None


# ── Prompt builder ────────────────────────────────────────────────


def _build_prompt(
    *,
    run_id: str,
    org_id: str,
    phase1_stats: dict[str, Any],
    phase2_signals: list[dict[str, Any]],
    review_mode: bool,
) -> str:
    """Render the consolidation prompt for the ``claude -p`` subagent.

    Mirrors the spec's pseudo-code exactly so test regressions catch
    accidental drift. The subagent must emit each ``PHASE_*_COMPLETE``
    marker as a trailing line, plus ``RUN_ID: <run_id>``.
    """
    stats_json = json.dumps(phase1_stats, indent=2, default=str)
    signal_lines = (
        "\n".join(
            f"  - [{s['pattern']}] ({s['file']}) {s['snippet']}"
            for s in phase2_signals[:60]
        )
        if phase2_signals
        else "  (no recent corrections / preferences detected)"
    )

    review_clause = (
        "\nREVIEW MODE: do NOT call consolidate / forget / update_memory.\n"
        f"Instead emit a markdown report to {_proposed_md_path(run_id)}\n"
        "describing what you WOULD do and exit. The user will run\n"
        "`lore dream apply <run_id>` to commit.\n"
        if review_mode
        else ""
    )

    return (
        "You are Lore's Dream worker. Consolidate the user's memory base.\n"
        f"Org: {org_id}\n"
        f"Run ID: {run_id}\n"
        f"{review_clause}\n"
        "Current state (Phase 1: Orient):\n"
        f"{stats_json}\n"
        "\n"
        "Recent session signal (Phase 2: Gather Signal):\n"
        f"{signal_lines}\n"
        "\n"
        "Your job (Phase 3 + 4):\n"
        "1. CONSOLIDATE near-duplicates: when multiple memories say the\n"
        "   same thing, call mcp__lore__consolidate_memories(\n"
        "       source_ids=[<id1>, <id2>, ...],\n"
        "       content=<merged narrative>,\n"
        "       type=<same type as inputs, e.g. 'lesson'>,\n"
        "       reason='consolidated near-duplicates').\n"
        "   This atomically creates the merged memory and supersedes\n"
        "   every source so provenance is preserved by construction —\n"
        "   prefer it over remember(...) + forget(...) for any merge.\n"
        "2. RESOLVE contradictions: query mcp__lore__conflicts; pick the\n"
        "   one supported by newer corrections.\n"
        "   When you see a correction in recent signal (e.g. \"actually X\n"
        "   is Y now\", \"we changed our approach\"), call\n"
        "   mcp__lore__supersede(old_memory_id, new_memory_id, reason)\n"
        "   on the loser. Supersede preserves the audit trail; forget\n"
        "   destroys it.\n"
        "   When two non-superseded memories contradict each other, call\n"
        "   mcp__lore__supersede on the older one with\n"
        "   reason='contradicted by mem_<NEW_ID>'.\n"
        "3. PROMOTE observations: when an observation has been retrieved\n"
        "   >3x and importance > 0.7, call mcp__lore__consolidate_memories(\n"
        "       source_ids=[<observation_id>],\n"
        "       content=<narrative>, type='lesson',\n"
        "       reason='promoted from observation').\n"
        "   This creates the lesson AND records a supersession event so\n"
        "   the lesson's provenance can be traced back to the source\n"
        "   observation via mcp__lore__provenance(<lesson_id>).\n"
        "4. PRUNE: when an observation is older than 30 days, importance\n"
        "   < 0.3, and access_count = 0, call mcp__lore__forget(memory_id).\n"
        "   Use this only for genuinely worthless observations — for\n"
        "   anything that might inform later recall, prefer step 1 or 3.\n"
        "5. NORMALIZE dates: if a memory contains 'yesterday' / 'last\n"
        "   week', call mcp__lore__update_memory to replace with absolute\n"
        "   dates (use today's date as anchor).\n"
        "\n"
        "Be surgical. Reorder/merge/prune; don't invent new content.\n"
        "\n"
        "When done, return on their own lines:\n"
        "  PHASE_1_ORIENT_COMPLETE\n"
        "  PHASE_2_SIGNAL_COMPLETE\n"
        "  PHASE_3_CONSOLIDATE_COMPLETE: <count_merged> <count_promoted>\n"
        "  PHASE_4_PRUNE_COMPLETE: <count_pruned>\n"
        f"  RUN_ID: {run_id}\n"
    )


# ── Subagent invocation ───────────────────────────────────────────


def _spawn_subagent(
    *,
    prompt: str,
    extract_log: Path,
) -> Optional[subprocess.Popen]:
    """Fire ``claude -p <prompt>`` as a detached subprocess.

    Returns the Popen handle so tests can introspect it. Returns None
    if ``claude`` is not on PATH.
    """
    if not shutil.which("claude"):
        return None
    extract_log.parent.mkdir(parents=True, exist_ok=True)
    log_fh = extract_log.open("a", encoding="utf-8")
    try:
        return subprocess.Popen(  # noqa: S603 — internal prompt
            # See cli/commands/capture.py for why both flags are required:
            # --verbose unblocks stream-json on Claude Code 2.1.x, and
            # --permission-mode=bypassPermissions stops every
            # mcp__lore__remember/supersede/consolidate_memories/forget
            # call from being denied with "you haven't granted it yet".
            # Dream is a trusted internal subagent; bypassing prompts is
            # the correct trust posture.
            [
                "claude", "-p", prompt,
                "--output-format", "stream-json",
                "--verbose",
                "--permission-mode", "bypassPermissions",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        log_fh.close()
        return None


def _parse_summary_from_log(extract_log: Path) -> dict[str, Any]:
    """Parse ``PHASE_*_COMPLETE`` markers + counts out of the subagent log.

    Returns a structured summary dict; absent markers default to None.
    """
    out: dict[str, Any] = {
        "phase_1_complete": False,
        "phase_2_complete": False,
        "phase_3_complete": False,
        "phase_4_complete": False,
        "phase_3_merged": None,
        "phase_3_promoted": None,
        "phase_4_pruned": None,
    }
    if not extract_log.exists():
        return out
    try:
        text = extract_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    if "PHASE_1_ORIENT_COMPLETE" in text:
        out["phase_1_complete"] = True
    if "PHASE_2_SIGNAL_COMPLETE" in text:
        out["phase_2_complete"] = True
    m3 = PHASE3_RE.search(text)
    if m3:
        out["phase_3_complete"] = True
        out["phase_3_merged"] = int(m3.group(1))
        out["phase_3_promoted"] = int(m3.group(2))
    elif "PHASE_3_CONSOLIDATE_COMPLETE" in text:
        out["phase_3_complete"] = True
    m4 = PHASE4_RE.search(text)
    if m4:
        out["phase_4_complete"] = True
        out["phase_4_pruned"] = int(m4.group(1))
    elif "PHASE_4_PRUNE_COMPLETE" in text:
        out["phase_4_complete"] = True
    return out


# ── Store helpers ─────────────────────────────────────────────────


def _resolve_database_url() -> str:
    return (
        os.environ.get("LORE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


async def _open_store():
    from lore.persistence.factory import make_store
    return await make_store(_resolve_database_url())


# ── Subcommand entry points ───────────────────────────────────────


def cmd_dream(args: argparse.Namespace) -> int:
    """``lore dream [--force] [--review] [--status] [--org-id ORG] [apply RUN_ID]``."""
    org_id = getattr(args, "org_id", None) or DEFAULT_ORG_ID
    status_only = getattr(args, "status", False)
    as_json = getattr(args, "as_json", False)
    force = getattr(args, "force", False)
    review_mode = getattr(args, "review", False)

    # Parse the variadic ``dream_args`` for ``apply <run_id>``.
    dream_args = getattr(args, "dream_args", None) or []
    apply_run_id: Optional[str] = None
    if dream_args:
        if len(dream_args) >= 2 and dream_args[0] == "apply":
            apply_run_id = dream_args[1]
        elif len(dream_args) == 1 and dream_args[0] == "apply":
            print(
                "`lore dream apply` requires a run_id argument.",
                file=sys.stderr,
            )
            return 1
        else:
            print(
                f"Unrecognized dream arguments: {dream_args!r}. "
                "Expected: `apply <run_id>`.",
                file=sys.stderr,
            )
            return 1

    if status_only:
        return _do_status(org_id=org_id, as_json=as_json)

    if apply_run_id:
        return _do_apply(run_id=apply_run_id, org_id=org_id)

    return _do_run(org_id=org_id, force=force, review_mode=review_mode)


def _do_status(*, org_id: str, as_json: bool) -> int:
    """Print current dream status; returns 0 on success."""
    from lore.services import dreams as svc

    async def _run() -> dict[str, Any]:
        store = await _open_store()
        try:
            return await svc.get_status(store, org_id)
        finally:
            await store.close()

    try:
        status = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        print(f"dream status failed: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(status, default=str))
        return 0

    print(f"Dream status (org={org_id}):")
    print(f"  Last run:           {status['last_run_at'] or '(never)'}")
    print(f"  Last status:        {status['last_run_status'] or 'n/a'}")
    print(f"  Sessions since:     {status['sessions_since_last']}")
    print(f"  Sessions required:  {status['sessions_required']}")
    print(f"  Next eligible at:   {status['next_eligible_at']}")
    print(f"  Eligible now:       {status['eligible_now']}")
    if status.get("last_summary"):
        print(f"  Last summary:       {status['last_summary']}")
    if status.get("last_error"):
        print(f"  Last error:         {status['last_error']}")
    return 0


def _do_apply(*, run_id: str, org_id: str) -> int:
    """Apply a previously-deferred ``--review`` dream.

    v1: re-spawn the subagent with the proposed.md instructions baked in,
    flagged so it actually mutates the DB. The proposed report must
    already exist on disk.
    """
    proposed = _proposed_md_path(run_id)
    if not proposed.exists():
        print(
            f"No proposed.md for run {run_id} at {proposed}; "
            "either the run never completed or it wasn't a --review run.",
            file=sys.stderr,
        )
        return 1
    print(
        f"--apply mode is a stub in v1. Inspect {proposed} manually,\n"
        "then re-run `lore dream` (without --review) to perform the\n"
        "consolidation. Future versions will replay the proposal.",
    )
    return 0


def _do_run(*, org_id: str, force: bool, review_mode: bool) -> int:
    """Run a dream now (subject to flock + eligibility)."""
    from lore.services import dreams as svc

    with _dreams_lock() as acquired:
        if not acquired:
            print("Another dream is in flight; skipping.", file=sys.stderr)
            return 0

        async def _run() -> int:
            store = await _open_store()
            try:
                if not force:
                    eligible = await svc.is_dream_eligible(store, org_id)
                    if not eligible:
                        print(
                            "Not eligible: 24h+5 sessions condition not met. "
                            "Use --force to override.",
                            file=sys.stderr,
                        )
                        return 0

                # Phase 1: Orient
                phase1 = await _gather_phase1_stats(store, org_id)

                # Insert the running row early so we have an id for the
                # output directory + the prompt.
                run = await svc.start_dream(store, org_id)
                run_id = run.id

                # Phase 2: Gather Signal (filesystem; no Store calls).
                transcripts = _list_recent_transcripts()
                phase2 = _grep_transcript_signals(transcripts)

                # Build prompt + spawn.
                prompt = _build_prompt(
                    run_id=run_id,
                    org_id=org_id,
                    phase1_stats=phase1,
                    phase2_signals=phase2,
                    review_mode=review_mode,
                )

                extract_log = _extract_log_path(run_id)
                proc = _spawn_subagent(prompt=prompt, extract_log=extract_log)
                if proc is None:
                    error = (
                        "claude binary not found on PATH; cannot spawn "
                        "consolidation subagent"
                    )
                    await svc.fail_dream(store, run_id, error)
                    print(error, file=sys.stderr)
                    return 1

                print(f"Dream started: run_id={run_id}")
                print(f"  Subagent PID: {proc.pid}")
                print(f"  Log:          {extract_log}")
                print(
                    "  Note: subagent runs detached. Status/summary will be\n"
                    "  picked up by the next `lore dream --status` once the\n"
                    "  subagent emits its phase markers."
                )
                # The subagent runs detached — we DO NOT await it here.
                # The summary will be filled in when the user (or next
                # dream invocation) sees the markers in extract.log and
                # complete_dream is called. For tests / synchronous use
                # callers may set LORE_DREAM_AWAIT=1 to block + parse.
                if os.environ.get("LORE_DREAM_AWAIT") == "1":
                    proc.wait(timeout=300)
                    summary = _parse_summary_from_log(extract_log)
                    await svc.complete_dream(store, run_id, summary)
                    print(f"  Summary:      {summary}")
                return 0
            finally:
                await store.close()

        try:
            return asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001
            print(f"dream run failed: {exc}", file=sys.stderr)
            return 1


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


__all__ = [
    "cmd_dream",
    "_build_prompt",
    "_grep_transcript_signals",
    "_gather_phase1_stats",
    "_list_recent_transcripts",
    "_parse_summary_from_log",
    "_dreams_lock",
    "_dreams_lock_path",
    "_dream_dir",
    "_extract_log_path",
    "_proposed_md_path",
    "_spawn_subagent",
    "PHASE3_RE",
    "PHASE4_RE",
    "TRANSCRIPT_SIGNAL_PATTERNS",
]
