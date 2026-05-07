"""``lore capture-extract`` — Phase 6A auto-capture subagent entry point.

This is the worker side of the auto-capture pipeline designed in
``docs/superpowers/specs/2026-05-07-lore-auto-capture-design.md``. It is
called by two hooks:

  * **PostToolUse** (``~/.claude/hooks/lore-capture-tool.sh``) — fires after
    ``LORE_CAPTURE_N`` (default 10) unprocessed events accumulate in the
    per-session buffer.
  * **Stop**         (``~/.claude/hooks/lore-capture-stop.sh``) —
    unconditionally fires on Claude Code's main-agent Stop event.

Both hooks call::

    lore capture-extract --session-id <sid> --transcript-path <path>

…which:

  1. Acquires a non-blocking ``flock`` on
     ``~/.lore/sessions/<sid>/lock``. If another extraction is in
     flight, this invocation no-ops.
  2. Reads the buffer at ``~/.lore/sessions/<sid>/buffer.jsonl`` and the
     cursor at ``~/.lore/sessions/<sid>/buffer.jsonl.cursor``.
  3. Slices the unprocessed tail (``seq > cursor``).
  4. Reads the last ``LORE_CAPTURE_TRANSCRIPT_TURNS`` user+assistant
     turns from the transcript.
  5. Pulls ``LORE_CAPTURE_RECENT_MEMORIES`` titles from
     ``${LORE_API_URL}/v1/memories`` for in-prompt dedup hints.
  6. Builds the extraction prompt and spawns ``claude -p`` as a fully
     detached subprocess. Output goes to
     ``~/.lore/sessions/<sid>/extract.log`` so we can inspect what the
     subagent did.
  7. The subagent's last line is expected to be
     ``PROCESSED_THROUGH_SEQ=<n>``. We watch the log non-blockingly for
     a short window after spawn; if we see it, we advance the cursor.
     If the subagent hasn't emitted by the time the parent process
     exits, the cursor stays put — the next batch retries the same
     events. Lore's vector-similarity dedup is the second line of
     defense.

The whole module is fail-open: every error path logs to
``~/.lore/sessions/<sid>/errors.log`` and returns 0 so Claude Code is
never disrupted by capture failures.

Open-question decisions made here (documented inline at first use):

  * **Q2 (truncation):** head 100 + ``…`` + tail 80 when value > 200
    chars; otherwise verbatim. See ``_truncate``.
  * **Q3 (cursor side-file vs SQLite row):** kept as a side file under
    ``~/.lore/sessions/<sid>/``. Phase 6E may revisit if it needs
    queryable session state.
  * **Q4 (Stop vs SubagentStop):** main-agent Stop only. SubagentStop
    fires after Task-tool subagents — wrong layer.
  * **Q5 (buffer cleanup):** files are left on disk for now; Phase 6E
    will own retention.
"""

from __future__ import annotations

import argparse
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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Constants & defaults ──────────────────────────────────────────

DEFAULT_TRANSCRIPT_TURNS = 50
DEFAULT_RECENT_MEMORIES = 20
DEFAULT_API_URL = "http://localhost:8765"

# How long to watch the subagent's log after spawn for a
# ``PROCESSED_THROUGH_SEQ=<n>`` line before we give up and let the
# subprocess keep running detached. We don't actually want to *wait*
# for the subagent — that would defeat the fire-and-forget design — so
# this defaults to 0 which means "spawn, return immediately, advance
# cursor only if a previous run left a marker on disk." Tests override
# this to read synchronously from a stub log file.
SUBAGENT_PROCESSED_RE = re.compile(r"PROCESSED_THROUGH_SEQ=(\d+)")

# Long content (very long Bash outputs, big diffs) gets truncated to
# 100-char head + ellipsis + 80-char tail, total ~181 chars. Threshold
# is 200 chars so we don't bother truncating things that already fit.
TRUNCATE_THRESHOLD = 200
TRUNCATE_HEAD = 100
TRUNCATE_TAIL = 80


# ── Path helpers ──────────────────────────────────────────────────


def _sanitize_session_id(session_id: str) -> str:
    """Restrict session_id to filesystem-safe chars; truncate to 64."""
    if not session_id:
        return "unknown"
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", session_id)[:64]
    return safe or "unknown"


def _sessions_root() -> Path:
    return Path.home() / ".lore" / "sessions"


def _session_dir(session_id: str) -> Path:
    return _sessions_root() / _sanitize_session_id(session_id)


def _buffer_path(session_id: str) -> Path:
    return _session_dir(session_id) / "buffer.jsonl"


def _cursor_path(session_id: str) -> Path:
    return _session_dir(session_id) / "buffer.jsonl.cursor"


def _lock_path(session_id: str) -> Path:
    return _session_dir(session_id) / "lock"


def _errors_log(session_id: str) -> Path:
    return _session_dir(session_id) / "errors.log"


def _extract_log(session_id: str) -> Path:
    return _session_dir(session_id) / "extract.log"


# ── Shared helpers ────────────────────────────────────────────────


def _log_error(session_id: str, msg: str) -> None:
    """Append a timestamped error line to ``errors.log`` (best-effort)."""
    try:
        path = _errors_log(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{ts}\t{msg}\n")
    except OSError:
        # Fail-open: never let logging itself break the hook.
        pass


def _truncate(value: Any) -> str:
    """Open-question #2: head 100 + … + tail 80 when > 200 chars.

    Non-string values are JSON-serialized first so callers can pass dicts
    and lists straight from the hook payload."""
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            value = str(value)
    if len(value) <= TRUNCATE_THRESHOLD:
        return value
    return value[:TRUNCATE_HEAD] + "…" + value[-TRUNCATE_TAIL:]


def _read_cursor(session_id: str) -> int:
    """Return the highest processed seq, or 0 if no cursor exists."""
    path = _cursor_path(session_id)
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def _write_cursor(session_id: str, seq: int) -> None:
    """Atomically advance the cursor (write tmpfile + rename)."""
    path = _cursor_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(str(int(seq)), encoding="utf-8")
    os.replace(tmp, path)


def _read_buffer(session_id: str) -> list[dict[str, Any]]:
    """Read all valid JSONL entries from the buffer file (skipping malformed)."""
    path = _buffer_path(session_id)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    _log_error(
                        session_id,
                        f"malformed buffer line skipped: {line[:120]}",
                    )
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except OSError as exc:
        _log_error(session_id, f"buffer read failed: {exc}")
    return out


def _read_transcript_tail(
    transcript_path: Optional[str],
    max_turns: int,
) -> str:
    """Read the last ``max_turns`` user+assistant turns from a Claude Code
    JSONL transcript and return them as plain text, oldest first.

    Falls back to an empty string when the file is missing or unreadable."""
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.exists():
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""

    out: list[str] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = obj.get("type")
        if kind not in ("user", "assistant"):
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")
        text: Optional[str] = None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for p_ in content:
                if isinstance(p_, dict):
                    t = p_.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            text = " ".join(parts) if parts else None
        if not text:
            continue
        text = text.strip()
        if not text:
            continue
        out.append(f"[{kind}] {text}")
        if len(out) >= max_turns:
            break

    return "\n".join(reversed(out))


def _fetch_recent_memory_titles(
    api_url: str,
    api_key: Optional[str],
    limit: int,
    timeout: float = 2.0,
) -> list[str]:
    """Pull recent memory previews from the Lore server. Best-effort —
    a missing/unreachable server returns ``[]`` and we proceed without
    dedup hints (the subagent still has Lore's vector dedup as a backstop)."""
    if not api_url:
        return []
    qs = urllib.parse.urlencode({"limit": int(limit)})
    url = f"{api_url.rstrip('/')}/v1/memories?{qs}"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except (OSError, urllib.error.URLError, ValueError):
        return []

    memories = data.get("memories") if isinstance(data, dict) else data
    if not isinstance(memories, list):
        return []
    titles: list[str] = []
    for m in memories:
        if not isinstance(m, dict):
            continue
        # Server returns full content; we use the first ~120 chars as a
        # title surrogate so the subagent has enough to spot duplicates
        # without bloating the prompt.
        content = m.get("title") or m.get("content") or ""
        if not isinstance(content, str):
            continue
        content = content.strip().replace("\n", " ")
        if not content:
            continue
        titles.append(content[:120])
    return titles


def _build_prompt(
    *,
    buffer_lines: list[str],
    transcript_tail: str,
    recent_titles: list[str],
) -> str:
    """Render the subagent prompt described in the spec.

    Kept identical in shape to the design doc so test regressions catch
    accidental drift. The subagent must return
    ``PROCESSED_THROUGH_SEQ=<n>`` as the trailing line."""
    buffer_block = "\n".join(buffer_lines) if buffer_lines else "(empty)"
    transcript_block = transcript_tail or "(no transcript available)"
    titles_block = (
        "\n".join(f"  - {t}" for t in recent_titles)
        if recent_titles
        else "  (none)"
    )
    return (
        "You are Lore's memory extraction worker for an active Claude Code\n"
        "session. Your job: read the session log and recent transcript, decide\n"
        "what (if anything) is worth remembering, and call Lore's MCP remember\n"
        "tool for each kept item.\n"
        "\n"
        "Inputs:\n"
        "  Buffer (tool calls since last extraction):\n"
        f"{buffer_block}\n"
        "\n"
        "  Transcript tail (recent user+assistant turns):\n"
        f"{transcript_block}\n"
        "\n"
        "  Memories already saved this session (do NOT re-save):\n"
        f"{titles_block}\n"
        "\n"
        "Goal: identify decisions, lessons, user preferences, gotchas, and key\n"
        "facts about the codebase or environment.\n"
        "\n"
        "For each kept item, call:\n"
        "  mcp__lore__remember(content=\"<short, self-contained>\", type=\"<one of:\n"
        "  lesson, fact, preference, pattern, convention, note>\")\n"
        "\n"
        "Rules:\n"
        "  - Be selective. Quality > quantity. 0 memories is fine.\n"
        "  - Typical batch: 0-3 memories.\n"
        "  - Skip trivial info-gathering, WIP noise, work the agent didn't finish.\n"
        "  - Skip anything similar to a memory already in the list above.\n"
        "  - Use complete sentences. The memory should make sense out of context.\n"
        "\n"
        "After processing, return on its own line: PROCESSED_THROUGH_SEQ=<highest seq from buffer>\n"
    )


def _spawn_subagent(
    *,
    prompt: str,
    extract_log: Path,
) -> Optional[subprocess.Popen]:
    """Fire-and-forget ``claude -p`` invocation. Returns the Popen handle
    so tests can introspect it (and so production code can keep the
    object alive long enough not to be GC'd before the OS forks)."""
    if not shutil.which("claude"):
        return None
    extract_log.parent.mkdir(parents=True, exist_ok=True)
    log_fh = extract_log.open("a", encoding="utf-8")
    try:
        return subprocess.Popen(  # noqa: S603 — input is internal, not user-supplied
            ["claude", "-p", prompt, "--output-format", "stream-json"],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        log_fh.close()
        return None


def _scan_log_for_processed_seq(extract_log: Path) -> Optional[int]:
    """Return the latest ``PROCESSED_THROUGH_SEQ=<n>`` value from
    extract.log, or None if no marker is present.

    Used after the subagent has been observed exiting (in tests) or
    when a previous detached run finished and left a marker behind.
    The most recent occurrence wins."""
    if not extract_log.exists():
        return None
    try:
        text = extract_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = SUBAGENT_PROCESSED_RE.findall(text)
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


# ── Concurrency ────────────────────────────────────────────────────


@contextlib.contextmanager
def _session_lock(session_id: str):
    """Acquire a non-blocking ``flock`` on ``<session_dir>/lock``.

    Yields ``True`` if acquired, ``False`` if another extraction is in
    flight. Either way the file descriptor is released on exit."""
    lock_path = _lock_path(session_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        _log_error(session_id, f"could not open lock: {exc}")
        yield False
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                yield False
                return
            _log_error(session_id, f"flock failed: {exc}")
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


# ── Subcommand entry points ────────────────────────────────────────


def cmd_capture_extract(args: argparse.Namespace) -> int:
    """Subagent dispatcher. Always returns 0 (fail-open)."""
    session_id = getattr(args, "session_id", None) or ""
    transcript_path = getattr(args, "transcript_path", None) or ""
    if not session_id:
        # Without a session id we have nothing to do.
        return 0

    with _session_lock(session_id) as acquired:
        if not acquired:
            _log_error(session_id, "extract skipped: another instance holds the lock")
            return 0

        try:
            _do_extract(session_id, transcript_path)
        except Exception as exc:  # pragma: no cover — last-ditch fail-open
            _log_error(session_id, f"unexpected extract failure: {exc!r}")
    return 0


def _do_extract(session_id: str, transcript_path: str) -> None:
    """The real worker, factored out so the lock context wraps it cleanly."""
    cursor = _read_cursor(session_id)
    buffer = _read_buffer(session_id)
    if not buffer:
        return

    # Slice unprocessed events.
    unprocessed = [
        e for e in buffer
        if isinstance(e.get("seq"), int) and e["seq"] > cursor
    ]
    if not unprocessed:
        return

    highest_seq = max(int(e["seq"]) for e in unprocessed)

    # Render each unprocessed event back to a JSONL line for the prompt.
    buffer_lines = [json.dumps(e, ensure_ascii=False, sort_keys=True) for e in unprocessed]

    transcript_turns = _env_int("LORE_CAPTURE_TRANSCRIPT_TURNS", DEFAULT_TRANSCRIPT_TURNS)
    transcript_tail = _read_transcript_tail(transcript_path, transcript_turns)

    api_url = os.environ.get("LORE_API_URL") or DEFAULT_API_URL
    api_key = os.environ.get("LORE_API_KEY") or _read_solo_key()
    recent_n = _env_int("LORE_CAPTURE_RECENT_MEMORIES", DEFAULT_RECENT_MEMORIES)
    recent_titles = _fetch_recent_memory_titles(api_url, api_key, recent_n)

    prompt = _build_prompt(
        buffer_lines=buffer_lines,
        transcript_tail=transcript_tail,
        recent_titles=recent_titles,
    )

    extract_log = _extract_log(session_id)

    proc = _spawn_subagent(prompt=prompt, extract_log=extract_log)
    if proc is None:
        _log_error(
            session_id,
            "claude binary not found on PATH; subagent spawn skipped",
        )
        return

    # Fire-and-forget. We do *not* block on the subagent. If a previous
    # detached run left a marker behind in extract.log, advance the
    # cursor to that value now — picking up the previous run's results.
    # The current run's marker (if it produces one) will be picked up
    # by the NEXT capture-extract invocation. This keeps the hook
    # non-blocking and idempotent.
    previous_seq = _scan_log_for_processed_seq(extract_log)
    if previous_seq is not None and previous_seq > cursor:
        # Defensive cap: never advance past the highest seq we know
        # exists in the buffer, in case a stale marker references a
        # later session's events.
        target = min(previous_seq, highest_seq)
        if target > cursor:
            _write_cursor(session_id, target)


def cmd_capture(args: argparse.Namespace) -> int:
    """Top-level dispatcher for ``lore capture-extract`` (and any future
    capture sub-subcommands). Currently routes everything to
    ``cmd_capture_extract``."""
    sub = getattr(args, "capture_subcommand", None) or "extract"
    if sub == "extract":
        return cmd_capture_extract(args)
    print(f"Unknown capture subcommand: {sub}", file=sys.stderr)
    return 1


# ── Misc ──────────────────────────────────────────────────────────


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _read_solo_key() -> Optional[str]:
    """Mirror of ``setup._read_solo_key`` — auto-bootstrap key file fallback.

    Duplicated rather than imported to keep this module's startup cost
    minimal (capture-extract is invoked from a hook on every Nth tool
    call, so import time matters)."""
    key_path = Path.home() / ".lore" / "key.txt"
    if not key_path.exists():
        return None
    try:
        return (key_path.read_text(encoding="utf-8").strip()) or None
    except OSError:
        return None


__all__ = [
    "cmd_capture",
    "cmd_capture_extract",
    "_truncate",
    "_read_cursor",
    "_write_cursor",
    "_read_buffer",
    "_build_prompt",
    "_session_dir",
    "_buffer_path",
    "_cursor_path",
    "_extract_log",
    "_session_lock",
    "_scan_log_for_processed_seq",
    "_sanitize_session_id",
]
