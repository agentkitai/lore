#!/usr/bin/env bash
# Lore UserPromptSubmit hook for Claude Code (Phase 6G).
# Installed by: lore setup claude-code
# Event: UserPromptSubmit
#
# Strips ``<private>...</private>`` blocks (fail-closed: an unbalanced
# opening tag strips to end-of-prompt) and appends a single
# ``{seq, ts, kind:"prompt", text}`` line to the per-session
# ``buffer.jsonl`` so the auto-capture subagent has user-intent signal
# alongside the existing tool-I/O events.
#
# No batching: prompts are infrequent compared to tool calls; the
# next PostToolUse / Stop / SessionEnd flush picks them up.
#
# All errors are absorbed: this hook always exits 0 so Claude Code is
# never disrupted by capture failures.
#
# Tunables:
#   LORE_AUTO_SAVE         master kill switch  (default: true)
#   LORE_PROMPT_MAX_BYTES  truncation cap      (default: 8192)

set +e

if [ "${LORE_AUTO_SAVE:-true}" = "false" ]; then
    exit 0
fi

LORE_HOME="${LORE_HOME:-$HOME/.lore}"
MAX="${LORE_PROMPT_MAX_BYTES:-8192}"

EVENT="$(cat)"
if [ -z "$EVENT" ]; then
    exit 0
fi

# All the parsing + <private> stripping + JSONL emission lives in
# Python; bash regex escaping for nested tags is more error-prone than
# it's worth, and we already need Python on PATH for the rest of the
# capture pipeline.
LORE_CAPTURE_EVENT="$EVENT" \
LORE_CAPTURE_HOME="$LORE_HOME" \
LORE_CAPTURE_MAX="$MAX" \
python3 -c '
import json, os, re, sys, time

event_raw = os.environ.get("LORE_CAPTURE_EVENT", "")
lore_home = os.environ.get("LORE_CAPTURE_HOME", os.path.expanduser("~/.lore"))
try:
    max_bytes = int(os.environ.get("LORE_CAPTURE_MAX", "8192"))
except ValueError:
    max_bytes = 8192

try:
    event = json.loads(event_raw or "{}")
except Exception:
    sys.exit(0)

sid = event.get("session_id") or ""
if not sid:
    sys.exit(0)

text = event.get("prompt") or ""

# Strip <private>...</private> (non-greedy, DOTALL, case-insensitive),
# then fail-closed by stripping any unbalanced opening tag to EOS.
text = re.sub(r"<private>.*?</private>", "", text, flags=re.S | re.I)
text = re.sub(r"<private>.*$",            "", text, flags=re.S | re.I)

# Cap by byte budget; decode-with-ignore handles a multi-byte rune
# straddling the boundary cleanly.
text_bytes = text.encode("utf-8")[:max_bytes]
text = text_bytes.decode("utf-8", errors="ignore").strip()
if not text:
    sys.exit(0)

# Sanitize the session id the same way capture.py does, so the path
# matches where the rest of the pipeline reads/writes.
safe_sid = re.sub(r"[^A-Za-z0-9_.\-]", "_", sid)[:64] or "unknown"
session_dir = os.path.join(lore_home, "sessions", safe_sid)
try:
    os.makedirs(session_dir, exist_ok=True)
except OSError:
    sys.exit(0)

buffer_path = os.path.join(session_dir, "buffer.jsonl")

seq = 1
if os.path.exists(buffer_path):
    try:
        with open(buffer_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                cur = obj.get("seq") if isinstance(obj, dict) else None
                if isinstance(cur, int) and cur >= seq:
                    seq = cur + 1
    except OSError:
        pass

entry = {
    "seq": seq,
    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "kind": "prompt",
    "text": text,
}

try:
    with open(buffer_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
except OSError:
    sys.exit(0)
'

exit 0
