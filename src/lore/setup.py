"""Lore setup — install auto-retrieval hooks for supported runtimes."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ── Hook script templates ──────────────────────────────────────────

CLAUDE_CODE_HOOK_SCRIPT = """\
#!/usr/bin/env python3
# Lore auto-retrieval hook for Claude Code
# Installed by: lore setup claude-code
#
# Improvements over v1 (PR #35):
#   1. Drops the unconditional "Recent Activity" dump that ignored relevance.
#   2. Per-session dedup: each memory is injected at most once per session,
#      tracked at /tmp/lore-session-<session_id>-seen.txt.
#   3. Conversation-aware retrieval: builds the query from the last few user
#      turns in the transcript, not just the most recent prompt — so deep
#      threads where the user types short follow-ups still surface
#      topic-relevant memories.
#
# Tunable via env vars:
#   LORE_API_URL          server URL (default: {server_url})
#   LORE_API_KEY          API key (default: ~/.lore/key.txt)
#   LORE_MIN_SCORE        relevance threshold (default: 0.5)
#   LORE_MAX_MEMORIES     max per turn (default: 5)
#   LORE_TURNS_FOR_QUERY  user turns to concat for retrieval (default: 4)
#   LORE_TIMEOUT          per-request seconds (default: 2)
#   LORE_PROGRESSIVE      Phase 6D progressive disclosure: search→detail (default: false)
#   LORE_SEARCH_LIMIT     when LORE_PROGRESSIVE=true, candidates fetched (default: 20)

import json
import os
import re
import sys
import urllib.parse
import urllib.request

DEFAULT_API_URL = "{server_url}"
DEFAULT_API_KEY = "{api_key}"


def _read_input():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {{}}


def _api_key():
    val = os.environ.get("LORE_API_KEY") or DEFAULT_API_KEY
    if val:
        return val
    # Final fallback: ~/.lore/key.txt (the bootstrap target)
    try:
        return (os.path.expanduser("~/.lore/key.txt") and
                open(os.path.expanduser("~/.lore/key.txt")).read().strip())
    except OSError:
        return ""


def _last_user_turns(transcript_path, max_turns):
    \"\"\"Read the last ``max_turns`` user messages from Claude Code's JSONL transcript.

    Returns the concatenated text (newest last). Falls back to empty string
    if the transcript can't be read.\"\"\"
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        lines = open(transcript_path, encoding="utf-8", errors="ignore").readlines()
    except OSError:
        return ""
    user_msgs = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Claude Code transcript: {{"type": "user", "message": {{"content": ...}}, ...}}
        if obj.get("type") != "user":
            continue
        msg = obj.get("message") or {{}}
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        if not isinstance(content, str):
            continue
        content = content.strip()
        if content:
            user_msgs.append(content)
        if len(user_msgs) >= max_turns:
            break
    return " \\n ".join(reversed(user_msgs))


def _retrieve(api_url, api_key, query, limit, min_score, timeout):
    qs = urllib.parse.urlencode({{
        "query": query,
        "format": "markdown",
        "limit": limit,
        "min_score": min_score,
    }})
    req = urllib.request.Request(
        f"{{api_url}}/v1/retrieve?{{qs}}",
        headers={{"Authorization": f"Bearer {{api_key}}"}},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return {{}}


def _search(api_url, api_key, query, limit, min_score, timeout):
    \"\"\"Phase 6D: compact-index search — id/title/score/signals only.\"\"\"
    qs = urllib.parse.urlencode({{
        "query": query,
        "limit": limit,
        "min_score": min_score,
    }})
    req = urllib.request.Request(
        f"{{api_url}}/v1/search?{{qs}}",
        headers={{"Authorization": f"Bearer {{api_key}}"}},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return {{}}


def _get_memories(api_url, api_key, ids, timeout):
    \"\"\"Phase 6D: fetch full payloads by id.\"\"\"
    if not ids:
        return {{}}
    qs = urllib.parse.urlencode({{"ids": ",".join(ids)}})
    req = urllib.request.Request(
        f"{{api_url}}/v1/memories/details?{{qs}}",
        headers={{"Authorization": f"Bearer {{api_key}}"}},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return {{}}


def _progressive_retrieve(api_url, api_key, query, search_limit, detail_limit, min_score, timeout):
    \"\"\"Two-phase progressive disclosure: search → drill-in.

    Returns a list of full memories shaped like ``/v1/retrieve``'s ``memories``
    field so the rest of ``main()`` (formatting + dedup) is unchanged.\"\"\"
    index = _search(api_url, api_key, query, search_limit, min_score, timeout)
    hits = index.get("hits") or []
    if not hits:
        return []
    survivors = [h for h in hits if h.get("score", 0) >= min_score][:detail_limit]
    if not survivors:
        return []
    score_by_id = {{h.get("id"): h.get("score", 0) for h in survivors}}
    detail = _get_memories(api_url, api_key, [h["id"] for h in survivors if h.get("id")], timeout)
    full = detail.get("memories") or []
    # Re-attach the search score so the existing markdown formatter stays
    # numerically meaningful (full payloads from /v1/memories/details don't
    # carry ``score``).
    out = []
    for m in full:
        m = dict(m)
        if "score" not in m:
            m["score"] = score_by_id.get(m.get("id"), 0)
        out.append(m)
    return out


def _seen_path(session_id):
    if not session_id:
        return None
    safe = re.sub(r"[^A-Za-z0-9_.\\-]", "_", str(session_id))[:64]
    return f"/tmp/lore-session-{{safe}}-seen.txt"


def _load_seen(path):
    if not path or not os.path.exists(path):
        return set()
    try:
        return set(line.strip() for line in open(path, encoding="utf-8") if line.strip())
    except OSError:
        return set()


def _append_seen(path, ids):
    if not path or not ids:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            for mid in ids:
                f.write(f"{{mid}}\\n")
    except OSError:
        pass


def main():
    inp = _read_input()
    prompt = (inp.get("prompt") or inp.get("user_message") or "").strip()
    if len(prompt) < 10:
        return

    api_url = os.environ.get("LORE_API_URL") or DEFAULT_API_URL
    api_key = _api_key()
    if not api_key:
        return

    min_score = float(os.environ.get("LORE_MIN_SCORE") or "0.5")
    limit = int(os.environ.get("LORE_MAX_MEMORIES") or "5")
    turns_for_query = int(os.environ.get("LORE_TURNS_FOR_QUERY") or "4")
    timeout = float(os.environ.get("LORE_TIMEOUT") or "2")
    progressive = (os.environ.get("LORE_PROGRESSIVE") or "false").lower() in ("true", "1", "yes")
    search_limit = int(os.environ.get("LORE_SEARCH_LIMIT") or "20")

    # Build conversation-aware query: previous user turns + current prompt.
    transcript_path = inp.get("transcript_path")
    conv_context = _last_user_turns(transcript_path, turns_for_query)
    query = (conv_context + " \\n " + prompt).strip() if conv_context else prompt

    if progressive:
        # Phase 6D: search compact index, drill into top survivors.
        memories = _progressive_retrieve(
            api_url, api_key, query, search_limit, limit, min_score, timeout
        )
    else:
        result = _retrieve(api_url, api_key, query, limit, min_score, timeout)
        memories = result.get("memories") or []
    if not memories:
        return

    # Per-session dedup: drop any IDs we've already injected this session.
    session_id = inp.get("session_id") or ""
    seen_path = _seen_path(session_id)
    seen = _load_seen(seen_path)
    fresh = [m for m in memories if m.get("id") not in seen]
    if not fresh:
        return

    # Build the markdown block ourselves (the server-side ``formatted`` field
    # would include already-seen entries).
    lines = ["## Relevant Memories"]
    for m in fresh:
        score = m.get("score", 0)
        content = (m.get("content") or "").replace("\\n", " ").strip()
        if content:
            lines.append(f"- **[{{score:.2f}}]** {{content}}")
    formatted = "\\n".join(lines) if len(lines) > 1 else ""
    if not formatted:
        return

    additional = "🧠 Relevant Memories:\\n" + formatted

    print(json.dumps({{
        "hookSpecificOutput": {{
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional,
        }}
    }}))

    # Mark these as seen so future turns this session skip them.
    _append_seen(seen_path, [m.get("id") for m in fresh if m.get("id")])


if __name__ == "__main__":
    main()
"""

OPENCLAW_HOOK_SCRIPT = """\
#!/usr/bin/env bash
# Lore auto-retrieval hook for OpenClaw
# Installed by: lore setup openclaw
# Event: message:preprocessed

set -euo pipefail

LORE_SERVER_URL="${{LORE_API_URL:-{server_url}}}"
LORE_KEY="${{LORE_API_KEY:-{api_key}}}"

INPUT=$(cat)
USER_MSG=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('content',''))" 2>/dev/null || echo "")

if [ -z "$USER_MSG" ] || [ "${{#USER_MSG}}" -lt 10 ]; then
    exit 0
fi

OUTPUT=""

# Fetch recent activity (unless disabled via LORE_RECENT_ACTIVITY=false)
if [ "${{LORE_RECENT_ACTIVITY:-true}}" != "false" ]; then
    RECENT_HOURS="${{LORE_RECENT_HOURS:-24}}"
    RECENT_RESULT=$(curl -sf -H "Authorization: Bearer $LORE_KEY" \\
        "$LORE_SERVER_URL/v1/recent?hours=$RECENT_HOURS&format=brief&max_memories=10" 2>/dev/null || echo "{{}}")
    RECENT_TEXT=$(echo "$RECENT_RESULT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if d.get('total_count',0)>0: print(d.get('formatted',''))
" 2>/dev/null || echo "")
    if [ -n "$RECENT_TEXT" ]; then
        OUTPUT="📋 Recent Activity (last ${{RECENT_HOURS}}h):\\n$RECENT_TEXT\\n\\n"
    fi
fi

# Query Lore for relevant memories
ENCODED=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$USER_MSG" 2>/dev/null || exit 0)
RESULT=$(curl -sf -H "Authorization: Bearer $LORE_KEY" \\
    "$LORE_SERVER_URL/v1/retrieve?query=$ENCODED&format=markdown&limit=5&min_score=0.3" 2>/dev/null || exit 0)

FORMATTED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('formatted',''))" 2>/dev/null || echo "")

if [ -n "$FORMATTED" ]; then
    OUTPUT="${{OUTPUT}}🧠 Relevant Memories:\\n$FORMATTED"
fi

if [ -n "$OUTPUT" ]; then
    echo -e "$OUTPUT"
fi
"""

CURSOR_HOOK_SCRIPT = """\
#!/usr/bin/env bash
# Lore auto-retrieval hook for Cursor
# Installed by: lore setup cursor
# Event: beforeSubmitPrompt

set -euo pipefail

LORE_SERVER_URL="${{LORE_API_URL:-{server_url}}}"
LORE_KEY="${{LORE_API_KEY:-{api_key}}}"

INPUT=$(cat)

# Extract prompt — try jq first, fall back to python3
if command -v jq &>/dev/null; then
    USER_MSG=$(echo "$INPUT" | jq -r '.user_message // .prompt // empty' 2>/dev/null || echo "")
else
    USER_MSG=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('user_message','') or d.get('prompt',''))" 2>/dev/null || echo "")
fi

if [ -z "$USER_MSG" ] || [ "${{#USER_MSG}}" -lt 10 ]; then
    exit 0
fi

OUTPUT=""

# Fetch recent activity (unless disabled via LORE_RECENT_ACTIVITY=false)
if [ "${{LORE_RECENT_ACTIVITY:-true}}" != "false" ]; then
    RECENT_HOURS="${{LORE_RECENT_HOURS:-24}}"
    RECENT_RESULT=$(curl -sf --max-time 2 -H "Authorization: Bearer $LORE_KEY" \\
        "$LORE_SERVER_URL/v1/recent?hours=$RECENT_HOURS&format=brief&max_memories=10" 2>/dev/null || echo "{{}}")
    RECENT_TEXT=$(echo "$RECENT_RESULT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if d.get('total_count',0)>0: print(d.get('formatted',''))
" 2>/dev/null || echo "")
    if [ -n "$RECENT_TEXT" ]; then
        OUTPUT="📋 Recent Activity (last ${{RECENT_HOURS}}h):\\n$RECENT_TEXT\\n\\n"
    fi
fi

# URL-encode — try jq first, fall back to python3
if command -v jq &>/dev/null; then
    ENCODED=$(printf '%s' "$USER_MSG" | jq -sRr @uri)
else
    ENCODED=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$USER_MSG" 2>/dev/null || exit 0)
fi

RESULT=$(curl -sf --max-time 2 -H "Authorization: Bearer $LORE_KEY" \\
    "$LORE_SERVER_URL/v1/retrieve?query=$ENCODED&format=markdown&limit=5&min_score=0.3" 2>/dev/null || exit 0)

if command -v jq &>/dev/null; then
    COUNT=$(echo "$RESULT" | jq -r '.count // 0' 2>/dev/null)
    if [ "$COUNT" -gt 0 ]; then
        FORMATTED=$(echo "$RESULT" | jq -r '.formatted // empty' 2>/dev/null)
        OUTPUT="${{OUTPUT}}🧠 Relevant Memories:\\n$FORMATTED"
    fi
else
    FORMATTED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('formatted',''))" 2>/dev/null || echo "")
    if [ -n "$FORMATTED" ]; then
        OUTPUT="${{OUTPUT}}🧠 Relevant Memories:\\n$FORMATTED"
    fi
fi

if [ -n "$OUTPUT" ]; then
    echo -e "$OUTPUT"
fi
"""

# Note: these two templates are stored as ordinary strings (not r-strings)
# because they go through ``str.format(server_url=..., api_key=...)`` at
# install time — which means every literal ``{`` and ``}`` in the bash
# source has to be doubled (``{{`` / ``}}``) to survive ``.format()``.
# After substitution the rendered scripts are validated with ``bash -n``.
LORE_CAPTURE_TOOL_HOOK_SCRIPT = """\
#!/usr/bin/env bash
# Lore auto-capture PostToolUse hook for Claude Code (Phase 6A).
# Installed by: lore setup claude-code
# Event: PostToolUse
#
# This hook is part of the auto-capture pipeline designed in
# docs/superpowers/specs/2026-05-07-lore-auto-capture-design.md. On every
# tool call:
#   - Check the LORE_AUTO_SAVE master kill switch (default true).
#   - Apply the skip list (LORE_CAPTURE_SKIP, CSV; defaults to passive
#     read tools + the agent's own todo scratchpad). mcp__lore__* is
#     ALWAYS skipped to prevent recursion when the subagent calls
#     remember() / remember_observation().
#   - Append a single JSON line to ~/.lore/sessions/<session_id>/buffer.jsonl
#     with seq, ts, tool, input_summary, output_summary (truncated).
#   - Compute unprocessed_count vs the cursor; if >= LORE_CAPTURE_N (default
#     10), spawn `lore capture-extract` as a fully detached subprocess.
#
# All errors are absorbed: this hook always exits 0 so Claude Code
# never breaks because of capture failures.
#
# Tunables:
#   LORE_AUTO_SAVE        master switch (default: true)
#   LORE_CAPTURE_N        events per batch (default: 10)
#   LORE_CAPTURE_SKIP     CSV of tool names to skip (overrides default)
#   LORE_CAPTURE_DEBUG    if true, log each step to errors.log

set +e

if [ "${{LORE_AUTO_SAVE:-true}}" = "false" ]; then
    exit 0
fi

INPUT="$(cat)"
if [ -z "$INPUT" ]; then
    exit 0
fi

# Default skip list. Tools listed here are filtered out before being
# appended to the buffer; their use surfaces indirectly via subsequent
# Edit/Bash/Write events.
DEFAULT_SKIP="Read,Glob,Grep,LS,BashOutput,ToolSearch,ListMcpResources,TodoWrite"
SKIP_LIST="${{LORE_CAPTURE_SKIP-$DEFAULT_SKIP}}"
BATCH_N="${{LORE_CAPTURE_N:-10}}"

# Pass the JSON payload to Python via stdin and the configuration via
# argv. Bash here-string handling for the JSON keeps multiline tool
# inputs/outputs intact.
LORE_CAPTURE_INPUT="$INPUT" \\
LORE_CAPTURE_SKIP_RUNTIME="$SKIP_LIST" \\
LORE_CAPTURE_BATCH_N="$BATCH_N" \\
python3 -c '
import json, os, re, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

lore_dir = Path.home() / ".lore"
skip_csv = os.environ.get("LORE_CAPTURE_SKIP_RUNTIME", "")
try:
    batch_n = int(os.environ.get("LORE_CAPTURE_BATCH_N", "10"))
except ValueError:
    batch_n = 10
debug = os.environ.get("LORE_CAPTURE_DEBUG", "").lower() in ("1","true","yes")

raw = os.environ.get("LORE_CAPTURE_INPUT", "")
try:
    inp = json.loads(raw or "{{}}")
except Exception:
    sys.exit(0)

tool_name = inp.get("tool_name") or ""
session_id = inp.get("session_id") or ""
transcript_path = inp.get("transcript_path") or ""
tool_input = inp.get("tool_input")
tool_response = inp.get("tool_response")

if not tool_name or not session_id:
    sys.exit(0)

# mcp__lore__* is ALWAYS skipped (recursion guard for the subagents
# own remember()/remember_observation() calls). Then apply the
# user-configurable skip list.
if tool_name.startswith("mcp__lore__"):
    sys.exit(0)
skip = {{s.strip() for s in skip_csv.split(",") if s.strip()}}
if tool_name in skip:
    sys.exit(0)

safe_sid = re.sub(r"[^A-Za-z0-9_.\\-]", "_", session_id)[:64] or "unknown"
session_dir = lore_dir / "sessions" / safe_sid
try:
    session_dir.mkdir(parents=True, exist_ok=True)
except OSError:
    sys.exit(0)

buffer_path = session_dir / "buffer.jsonl"
cursor_path = session_dir / "buffer.jsonl.cursor"
errors_path = session_dir / "errors.log"

def log_err(msg):
    try:
        ts = datetime.now(timezone.utc).isoformat()
        with errors_path.open("a", encoding="utf-8") as f:
            f.write(ts + "\\t" + msg + "\\n")
    except OSError:
        pass

def truncate(value):
    # Open-question #2 decision: head 100 + ellipsis + tail 80 above 200 chars.
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            value = str(value)
    if len(value) <= 200:
        return value
    return value[:100] + "…" + value[-80:]

seq = 1
if buffer_path.exists():
    try:
        with buffer_path.open("rb") as f:
            seq = sum(1 for _ in f) + 1
    except OSError:
        seq = 1

entry = {{
    "seq": seq,
    "ts": datetime.now(timezone.utc).isoformat(),
    "tool": tool_name,
    "input_summary": truncate(tool_input),
    "output_summary": truncate(tool_response),
}}

try:
    with buffer_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\\n")
except OSError as exc:
    log_err("buffer append failed: " + str(exc))
    sys.exit(0)

if debug:
    log_err("appended seq=" + str(seq) + " tool=" + tool_name)

cursor = 0
if cursor_path.exists():
    try:
        cursor = int(cursor_path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        cursor = 0

unprocessed = seq - cursor
if unprocessed < batch_n:
    sys.exit(0)

lore_bin = shutil.which("lore")
if not lore_bin:
    log_err("lore binary not found on PATH; capture-extract not spawned")
    sys.exit(0)

cmd = [lore_bin, "capture-extract", "--session-id", session_id]
if transcript_path:
    cmd += ["--transcript-path", transcript_path]

try:
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
except OSError as exc:
    log_err("failed to spawn capture-extract: " + str(exc))

sys.exit(0)
'

exit 0
"""

LORE_CAPTURE_STOP_HOOK_SCRIPT = """\
#!/usr/bin/env bash
# Lore auto-capture Stop hook for Claude Code (Phase 6A).
# Installed by: lore setup claude-code
# Event: Stop  (fires when the main agent stops; NOT SubagentStop)
#
# Unconditionally invokes `lore capture-extract` on whatever is in the
# session's buffer. The PostToolUse hook only fires capture-extract once
# every LORE_CAPTURE_N events, so the trailing N-1 events at end of
# session would otherwise be lost. This hook ensures every session
# flushes once at Stop.

set +e

if [ "${{LORE_AUTO_SAVE:-true}}" = "false" ]; then
    exit 0
fi

INPUT="$(cat)"
if [ -z "$INPUT" ]; then
    exit 0
fi

SESSION_ID="$(LORE_CAPTURE_INPUT="$INPUT" python3 -c '
import json, os, sys
try:
    print((json.loads(os.environ.get("LORE_CAPTURE_INPUT") or "{{}}") or {{}}).get("session_id",""))
except Exception:
    pass
' 2>/dev/null)"

TRANSCRIPT_PATH="$(LORE_CAPTURE_INPUT="$INPUT" python3 -c '
import json, os, sys
try:
    print((json.loads(os.environ.get("LORE_CAPTURE_INPUT") or "{{}}") or {{}}).get("transcript_path",""))
except Exception:
    pass
' 2>/dev/null)"

if [ -z "$SESSION_ID" ]; then
    exit 0
fi

if ! command -v lore >/dev/null 2>&1; then
    # Best-effort log; never fail the hook.
    SAFE_SID="$(printf '%s' "$SESSION_ID" | tr -c 'A-Za-z0-9_.-' '_' | cut -c1-64)"
    mkdir -p "$HOME/.lore/sessions/$SAFE_SID" 2>/dev/null || true
    printf '%s\\tlore binary not found on PATH; stop hook no-op\\n' \\
        "$(date -u +%FT%TZ)" \\
        >>"$HOME/.lore/sessions/$SAFE_SID/errors.log" 2>/dev/null || true
    exit 0
fi

# Fire-and-forget: the CLI itself spawns `claude -p` detached, but we
# wrap one more `nohup ... &` so this hook never blocks Claude Code's
# Stop event even if the CLI takes a beat to start up.
if [ -n "$TRANSCRIPT_PATH" ]; then
    nohup lore capture-extract \\
        --session-id "$SESSION_ID" \\
        --transcript-path "$TRANSCRIPT_PATH" \\
        >/dev/null 2>&1 &
else
    nohup lore capture-extract --session-id "$SESSION_ID" \\
        >/dev/null 2>&1 &
fi

exit 0
"""

LORE_DREAM_TRIGGER_HOOK_SCRIPT = """\
#!/usr/bin/env bash
# Lore dream trigger hook for Claude Code (Phase 6E).
# Installed by: lore setup claude-code
# Event: Stop (chained alongside the Phase 6A capture-stop hook)
#
# Checks `lore dream --status --json` for ``next_eligible_at``; if the
# 24h+5-sessions condition is met, fires `lore dream` as a fully detached
# subprocess and returns. Always exits 0 (fail-open) so Claude Code is
# never blocked by a dream check.
#
# Honors:
#   LORE_DREAM_AUTO        if false, exits 0 immediately (default true)
#   LORE_DATABASE_URL      passed through to `lore dream` subprocess

set +e

if [ "${{LORE_DREAM_AUTO:-true}}" = "false" ]; then
    exit 0
fi

if ! command -v lore >/dev/null 2>&1; then
    exit 0
fi

# Drain stdin so the Stop event payload doesn't backfill the pipe; we
# don't actually need any of it for the trigger check.
cat >/dev/null 2>&1 || true

STATUS_JSON="$(lore dream --status --json 2>/dev/null)"
if [ -z "$STATUS_JSON" ]; then
    exit 0
fi

ELIGIBLE="$(LORE_STATUS_JSON="$STATUS_JSON" python3 -c '
import json, os, sys
try:
    d = json.loads(os.environ.get("LORE_STATUS_JSON") or "{{}}") or {{}}
    print("yes" if d.get("eligible_now") else "no")
except Exception:
    print("no")
' 2>/dev/null)"

if [ "$ELIGIBLE" = "yes" ]; then
    nohup lore dream >/dev/null 2>&1 &
fi

exit 0
"""


CODEX_HOOK_SCRIPT = """\
#!/usr/bin/env bash
# Lore auto-retrieval hook for Codex CLI
# Installed by: lore setup codex
# Event: beforePlan

set -euo pipefail

LORE_SERVER_URL="${{LORE_API_URL:-{server_url}}}"
LORE_KEY="${{LORE_API_KEY:-{api_key}}}"

INPUT=$(cat)

# Extract prompt — try jq first, fall back to python3
if command -v jq &>/dev/null; then
    USER_MSG=$(echo "$INPUT" | jq -r '.prompt // .user_message // empty' 2>/dev/null || echo "")
else
    USER_MSG=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('prompt','') or d.get('user_message',''))" 2>/dev/null || echo "")
fi

if [ -z "$USER_MSG" ] || [ "${{#USER_MSG}}" -lt 10 ]; then
    exit 0
fi

OUTPUT=""

# Fetch recent activity (unless disabled via LORE_RECENT_ACTIVITY=false)
if [ "${{LORE_RECENT_ACTIVITY:-true}}" != "false" ]; then
    RECENT_HOURS="${{LORE_RECENT_HOURS:-24}}"
    RECENT_RESULT=$(curl -sf --max-time 2 -H "Authorization: Bearer $LORE_KEY" \\
        "$LORE_SERVER_URL/v1/recent?hours=$RECENT_HOURS&format=brief&max_memories=10" 2>/dev/null || echo "{{}}")
    RECENT_TEXT=$(echo "$RECENT_RESULT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if d.get('total_count',0)>0: print(d.get('formatted',''))
" 2>/dev/null || echo "")
    if [ -n "$RECENT_TEXT" ]; then
        OUTPUT="📋 Recent Activity (last ${{RECENT_HOURS}}h):\\n$RECENT_TEXT\\n\\n"
    fi
fi

# URL-encode — try jq first, fall back to python3
if command -v jq &>/dev/null; then
    ENCODED=$(printf '%s' "$USER_MSG" | jq -sRr @uri)
else
    ENCODED=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$USER_MSG" 2>/dev/null || exit 0)
fi

RESULT=$(curl -sf --max-time 2 -H "Authorization: Bearer $LORE_KEY" \\
    "$LORE_SERVER_URL/v1/retrieve?query=$ENCODED&format=markdown&limit=5&min_score=0.3" 2>/dev/null || exit 0)

if command -v jq &>/dev/null; then
    COUNT=$(echo "$RESULT" | jq -r '.count // 0' 2>/dev/null)
    if [ "$COUNT" -gt 0 ]; then
        FORMATTED=$(echo "$RESULT" | jq -r '.formatted // empty' 2>/dev/null)
        OUTPUT="${{OUTPUT}}🧠 Relevant Memories:\\n$FORMATTED"
    fi
else
    FORMATTED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('formatted',''))" 2>/dev/null || echo "")
    if [ -n "$FORMATTED" ]; then
        OUTPUT="${{OUTPUT}}🧠 Relevant Memories:\\n$FORMATTED"
    fi
fi

if [ -n "$OUTPUT" ]; then
    echo -e "$OUTPUT"
fi
"""


# ── Paths ──────────────────────────────────────────────────────────

def _read_solo_key() -> Optional[str]:
    """Return the auto-bootstrapped key written by Phase 3J at ``~/.lore/key.txt``,
    or None if the file is missing/empty.

    Used as a final fallback by ``setup_*`` so users running ``lore setup
    claude-code`` after ``lore serve`` once don't have to pass ``--api-key``
    or set ``LORE_API_KEY`` manually.
    """
    key_path = Path.home() / ".lore" / "key.txt"
    if not key_path.exists():
        return None
    try:
        key = key_path.read_text().strip()
        return key or None
    except OSError:
        return None


def _claude_hooks_dir() -> Path:
    return Path.home() / ".claude" / "hooks"


def _claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _claude_hook_path() -> Path:
    return _claude_hooks_dir() / "lore-retrieve.sh"


def _claude_capture_tool_hook_path() -> Path:
    """Phase 6A — PostToolUse auto-capture hook (~/.claude/hooks/lore-capture-tool.sh)."""
    return _claude_hooks_dir() / "lore-capture-tool.sh"


def _claude_capture_stop_hook_path() -> Path:
    """Phase 6A — Stop auto-capture hook (~/.claude/hooks/lore-capture-stop.sh)."""
    return _claude_hooks_dir() / "lore-capture-stop.sh"


def _claude_dream_trigger_hook_path() -> Path:
    """Phase 6E — Stop dream trigger hook (~/.claude/hooks/lore-dream-trigger.sh)."""
    return _claude_hooks_dir() / "lore-dream-trigger.sh"


def _openclaw_hooks_dir() -> Path:
    """OpenClaw hooks directory (workspace-level)."""
    return Path.home() / ".openclaw" / "hooks"


def _openclaw_hook_path() -> Path:
    return _openclaw_hooks_dir() / "lore-retrieve.sh"


def _cursor_hooks_dir() -> Path:
    """Cursor hooks directory (project-level)."""
    return Path.cwd() / ".cursor" / "hooks"


def _cursor_hook_path() -> Path:
    return _cursor_hooks_dir() / "lore-retrieve.sh"


def _cursor_config_path() -> Path:
    return Path.cwd() / ".cursor" / "hooks" / "config.json"


def _codex_hooks_dir() -> Path:
    """Codex hooks directory (global)."""
    return Path.home() / ".codex" / "hooks"


def _codex_hook_path() -> Path:
    return _codex_hooks_dir() / "lore-retrieve.sh"


def _codex_config_path() -> Path:
    return Path.cwd() / "codex.yaml"


# ── Validation / connection helpers ────────────────────────────────


def _backup_config(path: Path) -> Optional[Path]:
    """Create a timestamped backup of a config file. Keeps max 3 backups."""
    if not path.exists():
        return None

    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.parent / f"{path.name}.lore-backup.{timestamp}"

    import shutil
    shutil.copy2(path, backup_path)

    # Prune old backups (keep max 3)
    pattern = f"{path.name}.lore-backup.*"
    backups = sorted(path.parent.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_backup in backups[3:]:
        old_backup.unlink(missing_ok=True)

    return backup_path


def _validate_hook(hook_path: Path) -> list[str]:
    """Validate a hook script: bash syntax check + execute permission."""
    errors: list[str] = []
    if not hook_path.exists():
        errors.append(f"Hook file does not exist: {hook_path}")
        return errors

    # Check execute permission
    if not os.access(hook_path, os.X_OK):
        errors.append(f"Hook is not executable: {hook_path}")

    # Bash syntax check
    try:
        result = subprocess.run(
            ["bash", "-n", str(hook_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            errors.append(f"Bash syntax error: {result.stderr.strip()}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # bash not available, skip syntax check

    return errors


def _validate_config(config_path: Path, runtime: str) -> list[str]:
    """Validate a config file: JSON/YAML syntax + required keys."""
    errors: list[str] = []
    if not config_path.exists():
        errors.append(f"Config file does not exist: {config_path}")
        return errors

    content = config_path.read_text()

    if config_path.suffix == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON: {e}")
            return errors

        # Check for hooks key
        if "hooks" not in data:
            errors.append("Config missing 'hooks' key")
    elif config_path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            data = yaml.safe_load(content)
            if data is None:
                errors.append("Config file is empty")
            elif "hooks" not in (data or {}):
                errors.append("Config missing 'hooks' key")
        except ImportError:
            pass  # Can't validate YAML without PyYAML
        except Exception as e:
            errors.append(f"Invalid YAML: {e}")

    return errors


def _test_connection(server_url: str, api_key: Optional[str] = None) -> dict:
    """Test connectivity to a Lore server."""
    import time
    import urllib.error
    import urllib.request

    result: dict = {"status": "unknown", "health": None, "retrieve": None, "latency_ms": 0}
    start = time.monotonic()

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Test /health
    try:
        req = urllib.request.Request(f"{server_url}/health", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            result["health"] = resp.status == 200
    except Exception as e:
        result["health"] = False
        result["error"] = str(e)

    # Test /v1/retrieve (requires auth)
    if api_key and result["health"]:
        try:
            import urllib.parse
            query = urllib.parse.quote("test")
            req = urllib.request.Request(
                f"{server_url}/v1/retrieve?query={query}&limit=1",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result["retrieve"] = resp.status == 200
        except Exception:
            result["retrieve"] = False

    result["latency_ms"] = round((time.monotonic() - start) * 1000, 2)
    result["status"] = "ok" if result["health"] else "unreachable"

    return result


def _show_rollback_instructions(runtime: str, backup_paths: list[Path]) -> None:
    """Print instructions for rolling back to backup configs."""
    if not backup_paths:
        print("  No backups to restore from.")
        return
    print("  To rollback, restore from backups:")
    for bp in backup_paths:
        original = bp.parent / bp.name.split(".lore-backup.")[0]
        print(f"    cp {bp} {original}")


# ── Setup functions ────────────────────────────────────────────────


def _claude_hook_already_registered(event_hooks: list, hook_cmd: str) -> bool:
    """Return True if ``hook_cmd`` is already in the given event hook list.

    Supports both the legacy flat shape (``[{"command": "..."}, ...]``) and
    the current matcher-grouped shape (``[{"matcher": "", "hooks": [...]}]``)."""
    for h in event_hooks:
        if not isinstance(h, dict):
            continue
        if h.get("command") == hook_cmd:
            return True
        for inner in h.get("hooks", []) or []:
            if isinstance(inner, dict) and inner.get("command") == hook_cmd:
                return True
    return False


def _register_claude_hook(settings: dict, event_name: str, hook_cmd: str) -> bool:
    """Append a hook entry under ``settings["hooks"][event_name]`` if not
    already registered. Returns True if newly added."""
    hooks = settings.setdefault("hooks", {})
    event_hooks = hooks.setdefault(event_name, [])
    if _claude_hook_already_registered(event_hooks, hook_cmd):
        return False
    event_hooks.append({
        "matcher": "",
        "hooks": [
            {"type": "command", "command": hook_cmd},
        ],
    })
    return True


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def setup_claude_code(server_url: str = "http://localhost:8765", api_key: str | None = None) -> None:
    """Install Lore hooks for Claude Code.

    As of Phase 6E this installs four hooks under ``~/.claude/hooks/``:

      * ``lore-retrieve.sh``        — UserPromptSubmit auto-retrieval
        (existing behavior).
      * ``lore-capture-tool.sh``    — PostToolUse buffer-append +
        N-batched ``lore capture-extract`` spawn (Phase 6A).
      * ``lore-capture-stop.sh``    — Stop hook that flushes the
        trailing capture batch unconditionally (Phase 6A).
      * ``lore-dream-trigger.sh``   — Stop hook that fires
        ``lore dream`` when the 24h+5-sessions condition is met
        (Phase 6E). Coexists with the capture-stop hook on Stop.

    All are registered in ``~/.claude/settings.json`` under the
    appropriate Claude Code event names. The capture/dream hooks fail
    open and inherit the user's existing Claude Code auth — no extra
    configuration needed.
    """
    hooks_dir = _claude_hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)

    api_key_val = api_key or os.environ.get("LORE_API_KEY") or _read_solo_key()
    if not api_key_val:
        print(
            "  WARNING: no API key. Pass --api-key, set LORE_API_KEY, or run "
            "`lore serve` once to auto-bootstrap ~/.lore/key.txt."
        )

    # ── 1. UserPromptSubmit retrieval hook (existing) ─────────────────
    retrieve_hook = _claude_hook_path()
    retrieve_script = CLAUDE_CODE_HOOK_SCRIPT.format(
        server_url=server_url, api_key=api_key_val,
    )
    _write_executable(retrieve_hook, retrieve_script)
    print(f"  Retrieval hook (UserPromptSubmit): {retrieve_hook}")

    # ── 2. PostToolUse auto-capture hook (Phase 6A) ───────────────────
    capture_tool_hook = _claude_capture_tool_hook_path()
    capture_tool_script = LORE_CAPTURE_TOOL_HOOK_SCRIPT.format(
        server_url=server_url, api_key=api_key_val,
    )
    _write_executable(capture_tool_hook, capture_tool_script)
    print(f"  Capture hook  (PostToolUse):       {capture_tool_hook}")

    # ── 3. Stop auto-capture flush hook (Phase 6A) ────────────────────
    capture_stop_hook = _claude_capture_stop_hook_path()
    capture_stop_script = LORE_CAPTURE_STOP_HOOK_SCRIPT.format(
        server_url=server_url, api_key=api_key_val,
    )
    _write_executable(capture_stop_hook, capture_stop_script)
    print(f"  Capture hook  (Stop):              {capture_stop_hook}")

    # ── 4. Dream trigger hook (Phase 6E) ──────────────────────────────
    # Wired alongside the capture-stop hook on the Stop event chain.
    dream_trigger_hook = _claude_dream_trigger_hook_path()
    dream_trigger_script = LORE_DREAM_TRIGGER_HOOK_SCRIPT.format()
    _write_executable(dream_trigger_hook, dream_trigger_script)
    print(f"  Dream hook    (Stop):              {dream_trigger_hook}")

    # ── settings.json registration ────────────────────────────────────
    settings_path = _claude_settings_path()
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    added_retrieve = _register_claude_hook(
        settings, "UserPromptSubmit", str(retrieve_hook),
    )
    added_capture = _register_claude_hook(
        settings, "PostToolUse", str(capture_tool_hook),
    )
    added_stop = _register_claude_hook(
        settings, "Stop", str(capture_stop_hook),
    )
    added_dream = _register_claude_hook(
        settings, "Stop", str(dream_trigger_hook),
    )

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  Settings:    {settings_path}")

    flags = []
    if added_retrieve:
        flags.append("UserPromptSubmit registered")
    if added_capture:
        flags.append("PostToolUse registered")
    if added_stop:
        flags.append("Stop registered")
    if added_dream:
        flags.append("Stop dream trigger registered")
    if flags:
        print("  " + "; ".join(flags))
    print("Claude Code hooks installed successfully (4 hooks: UserPromptSubmit, PostToolUse, Stop x2).")


def setup_openclaw(server_url: str = "http://localhost:8765", api_key: str | None = None) -> None:
    """Install Lore auto-retrieval hook for OpenClaw."""
    hooks_dir = _openclaw_hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_path = _openclaw_hook_path()
    api_key_val = api_key or os.environ.get("LORE_API_KEY") or _read_solo_key() or ""

    script = OPENCLAW_HOOK_SCRIPT.format(server_url=server_url, api_key=api_key_val)
    hook_path.write_text(script)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  Hook script: {hook_path}")
    print("OpenClaw hook installed successfully.")


def setup_cursor(server_url: str = "http://localhost:8765", api_key: str | None = None) -> None:
    """Install Lore auto-retrieval hook for Cursor."""
    hooks_dir = _cursor_hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_path = _cursor_hook_path()
    api_key_val = api_key or os.environ.get("LORE_API_KEY") or _read_solo_key() or ""

    # Write hook script
    script = CURSOR_HOOK_SCRIPT.format(server_url=server_url, api_key=api_key_val)
    hook_path.write_text(script)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  Hook script: {hook_path}")

    # Update config.json
    config_path = _cursor_config_path()
    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    hooks = config.setdefault("hooks", {})
    before_submit = hooks.setdefault("beforeSubmitPrompt", [])

    hook_entry = {"command": str(hook_path)}
    already_exists = any(
        h.get("command") == str(hook_path)
        for h in before_submit
        if isinstance(h, dict)
    )
    if not already_exists:
        before_submit.append(hook_entry)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"  Config:      {config_path}")
    print("Cursor hook installed successfully.")


def setup_codex(server_url: str = "http://localhost:8765", api_key: str | None = None) -> None:
    """Install Lore auto-retrieval hook for Codex CLI."""
    hooks_dir = _codex_hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_path = _codex_hook_path()
    api_key_val = api_key or os.environ.get("LORE_API_KEY") or _read_solo_key() or ""

    # Write hook script
    script = CODEX_HOOK_SCRIPT.format(server_url=server_url, api_key=api_key_val)
    hook_path.write_text(script)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  Hook script: {hook_path}")

    # Update codex.yaml
    config_path = _codex_config_path()
    hook_cmd = str(hook_path)

    if config_path.exists():
        content = config_path.read_text()
        # Check if already registered (idempotent)
        if hook_cmd in content:
            print(f"  Config:      {config_path} (already registered)")
            print("Codex hook installed successfully.")
            return

        # Try PyYAML, fall back to manual append
        try:
            import yaml
            data = yaml.safe_load(content) or {}
            hooks = data.setdefault("hooks", {})
            hooks["beforePlan"] = {"command": hook_cmd}
            config_path.write_text(yaml.dump(data, default_flow_style=False))
        except ImportError:
            # Manual append
            if "hooks:" not in content:
                content += f"\nhooks:\n  beforePlan:\n    command: {hook_cmd}\n"
            else:
                content += f"  beforePlan:\n    command: {hook_cmd}\n"
            config_path.write_text(content)
    else:
        # Create new file — try PyYAML, fall back to manual
        try:
            import yaml
            data = {"hooks": {"beforePlan": {"command": hook_cmd}}}
            config_path.write_text(yaml.dump(data, default_flow_style=False))
        except ImportError:
            config_path.write_text(f"hooks:\n  beforePlan:\n    command: {hook_cmd}\n")

    print(f"  Config:      {config_path}")
    print("Codex hook installed successfully.")


def show_status() -> None:
    """Show installation status for all supported runtimes."""
    print("Lore Setup Status")
    print("=" * 40)

    # Claude Code
    retrieve_hook = _claude_hook_path()
    capture_tool_hook = _claude_capture_tool_hook_path()
    capture_stop_hook = _claude_capture_stop_hook_path()
    settings = _claude_settings_path()
    print("\nClaude Code:")
    print(f"  Retrieval hook (UserPromptSubmit): {retrieve_hook} "
          f"{'[installed]' if retrieve_hook.exists() else '[not installed]'}")
    print(f"  Capture hook   (PostToolUse):      {capture_tool_hook} "
          f"{'[installed]' if capture_tool_hook.exists() else '[not installed]'}")
    print(f"  Capture hook   (Stop):             {capture_stop_hook} "
          f"{'[installed]' if capture_stop_hook.exists() else '[not installed]'}")
    if settings.exists():
        try:
            s = json.loads(settings.read_text())
            for event, hook in (
                ("UserPromptSubmit", retrieve_hook),
                ("PostToolUse", capture_tool_hook),
                ("Stop", capture_stop_hook),
            ):
                hooks = s.get("hooks", {}).get(event, []) or []
                registered = _claude_hook_already_registered(hooks, str(hook))
                print(f"  Settings ({event}): "
                      f"{'[registered]' if registered else '[not registered]'}")
        except (json.JSONDecodeError, OSError):
            print(f"  Settings: {settings} [error reading]")
    else:
        print(f"  Settings: {settings} [not found]")

    # OpenClaw
    hook = _openclaw_hook_path()
    print("\nOpenClaw:")
    print(f"  Hook:     {hook} {'[installed]' if hook.exists() else '[not installed]'}")

    # Cursor
    hook = _cursor_hook_path()
    config = _cursor_config_path()
    print("\nCursor:")
    print(f"  Hook:     {hook} {'[installed]' if hook.exists() else '[not installed]'}")
    if config.exists():
        try:
            c = json.loads(config.read_text())
            hooks = c.get("hooks", {}).get("beforeSubmitPrompt", [])
            registered = any(
                h.get("command") == str(hook)
                for h in hooks
                if isinstance(h, dict)
            )
            print(f"  Config:   {config} {'[registered]' if registered else '[not registered]'}")
        except (json.JSONDecodeError, OSError):
            print(f"  Config:   {config} [error reading]")
    else:
        print(f"  Config:   {config} [not found]")

    # Codex
    hook = _codex_hook_path()
    config = _codex_config_path()
    print("\nCodex:")
    print(f"  Hook:     {hook} {'[installed]' if hook.exists() else '[not installed]'}")
    if config.exists():
        content = config.read_text()
        registered = str(hook) in content
        print(f"  Config:   {config} {'[registered]' if registered else '[not registered]'}")
    else:
        print(f"  Config:   {config} [not found]")


def remove_runtime(runtime: str) -> None:
    """Remove Lore hooks for a runtime."""
    if runtime == "claude-code":
        # Remove all three Claude Code hook scripts (Phase 6A added two more).
        hooks_to_remove = [
            ("UserPromptSubmit", _claude_hook_path()),
            ("PostToolUse", _claude_capture_tool_hook_path()),
            ("Stop", _claude_capture_stop_hook_path()),
        ]
        for _, hook in hooks_to_remove:
            if hook.exists():
                hook.unlink()
                print(f"  Removed hook: {hook}")

        # Remove from settings.json under each event name.
        settings_path = _claude_settings_path()
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text())
                hooks_section = settings.get("hooks", {})
                for event_name, hook in hooks_to_remove:
                    hook_cmd = str(hook)
                    event_list = hooks_section.get(event_name, []) or []
                    hooks_section[event_name] = [
                        h for h in event_list
                        if not (
                            (isinstance(h, dict) and h.get("command") == hook_cmd)
                            or (
                                isinstance(h, dict)
                                and any(
                                    inner.get("command") == hook_cmd
                                    for inner in h.get("hooks", [])
                                    if isinstance(inner, dict)
                                )
                            )
                        )
                    ]
                settings_path.write_text(json.dumps(settings, indent=2) + "\n")
                print(f"  Updated settings: {settings_path}")
            except (json.JSONDecodeError, OSError):
                pass
        print("Claude Code hooks removed.")

    elif runtime == "openclaw":
        hook = _openclaw_hook_path()
        if hook.exists():
            hook.unlink()
            print(f"  Removed hook: {hook}")
        print("OpenClaw hooks removed.")

    elif runtime == "cursor":
        hook = _cursor_hook_path()
        if hook.exists():
            hook.unlink()
            print(f"  Removed hook: {hook}")

        # Remove from config.json
        config_path = _cursor_config_path()
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
                hooks = config.get("hooks", {}).get("beforeSubmitPrompt", [])
                config["hooks"]["beforeSubmitPrompt"] = [
                    h for h in hooks
                    if not (isinstance(h, dict) and h.get("command") == str(hook))
                ]
                config_path.write_text(json.dumps(config, indent=2) + "\n")
                print(f"  Updated config: {config_path}")
            except (json.JSONDecodeError, OSError):
                pass
        print("Cursor hooks removed.")

    elif runtime == "codex":
        hook = _codex_hook_path()
        if hook.exists():
            hook.unlink()
            print(f"  Removed hook: {hook}")

        # Remove hook entry from codex.yaml
        config_path = _codex_config_path()
        if config_path.exists():
            try:
                import yaml
                data = yaml.safe_load(config_path.read_text()) or {}
                if "hooks" in data and "beforePlan" in data.get("hooks", {}):
                    del data["hooks"]["beforePlan"]
                    if not data["hooks"]:
                        del data["hooks"]
                    config_path.write_text(yaml.dump(data, default_flow_style=False) if data else "")
                    print(f"  Updated config: {config_path}")
            except ImportError:
                # Without PyYAML, remove lines containing the hook path
                content = config_path.read_text()
                if str(hook) in content:
                    lines = content.splitlines(keepends=True)
                    lines = [l for l in lines if str(hook) not in l]
                    config_path.write_text("".join(lines))
                    print(f"  Updated config: {config_path}")
        print("Codex hooks removed.")

    else:
        print(f"Unknown runtime: {runtime}", file=sys.stderr)
        sys.exit(1)
