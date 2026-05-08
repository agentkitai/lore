#!/usr/bin/env bash
# Lore SessionEnd hook for Claude Code (Phase 6G).
# Installed by: lore setup claude-code
# Event: SessionEnd
#
# Pipeline:
#   1. Foreground ``lore capture-extract --foreground`` — flushes any
#      remaining buffer entries through the extraction subagent
#      synchronously so step 2 sees the per-batch observations the
#      flush just wrote. (This is the one place the otherwise
#      detached extract path is intentionally inverted.)
#   2. ``lore session-finalize --session-id <sid>`` — spawns one
#      small subagent to emit a single ``meta.kind="summary"``
#      observation reading only the per-batch observations, then
#      writes ``~/.lore/sessions/<sid>/sealed``.
#
# All errors are absorbed: this hook always exits 0 so Claude Code
# is never blocked by capture failures.

set +e

if [ "${LORE_AUTO_SAVE:-true}" = "false" ]; then
    exit 0
fi

LORE_HOME="${LORE_HOME:-$HOME/.lore}"
EVENT="$(cat)"
if [ -z "$EVENT" ]; then
    exit 0
fi

SID="$(LORE_CAPTURE_INPUT="$EVENT" python3 -c '
import json, os, sys
try:
    print((json.loads(os.environ.get("LORE_CAPTURE_INPUT") or "{}") or {}).get("session_id",""))
except Exception:
    pass
' 2>/dev/null)"

if [ -z "$SID" ]; then
    exit 0
fi

TRANSCRIPT="$(LORE_CAPTURE_INPUT="$EVENT" python3 -c '
import json, os, sys
try:
    print((json.loads(os.environ.get("LORE_CAPTURE_INPUT") or "{}") or {}).get("transcript_path",""))
except Exception:
    pass
' 2>/dev/null)"

# Sanitize the session id the same way capture.py does so the path
# matches where the rest of the pipeline reads/writes.
SAFE_SID="$(printf '%s' "$SID" | tr -c 'A-Za-z0-9_.-' '_' | cut -c1-64)"
[ -z "$SAFE_SID" ] && SAFE_SID="unknown"

ERRORS_LOG="$LORE_HOME/sessions/$SAFE_SID/errors.log"
mkdir -p "$LORE_HOME/sessions/$SAFE_SID" 2>/dev/null || true

if ! command -v lore >/dev/null 2>&1; then
    {
        printf '%s\tlore binary not found on PATH; SessionEnd hook no-op\n' \
            "$(date -u +%FT%TZ)"
    } >>"$ERRORS_LOG" 2>/dev/null || true
    exit 0
fi

# Step 1: foreground flush. We want session-finalize to see the
# observations the subagent just wrote, so this MUST be synchronous —
# unlike the Stop hook which detaches.
if [ -n "$TRANSCRIPT" ]; then
    lore capture-extract \
        --session-id "$SID" \
        --transcript-path "$TRANSCRIPT" \
        --foreground \
        2>>"$ERRORS_LOG" || true
else
    lore capture-extract \
        --session-id "$SID" \
        --foreground \
        2>>"$ERRORS_LOG" || true
fi

# Step 2: emit the session summary observation and write the sealed marker.
lore session-finalize --session-id "$SID" 2>>"$ERRORS_LOG" || true

exit 0
