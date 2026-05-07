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

    # Build conversation-aware query: previous user turns + current prompt.
    transcript_path = inp.get("transcript_path")
    conv_context = _last_user_turns(transcript_path, turns_for_query)
    query = (conv_context + " \\n " + prompt).strip() if conv_context else prompt

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


def setup_claude_code(server_url: str = "http://localhost:8765", api_key: str | None = None) -> None:
    """Install Lore auto-retrieval hook for Claude Code."""
    hooks_dir = _claude_hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_path = _claude_hook_path()
    api_key_val = api_key or os.environ.get("LORE_API_KEY") or _read_solo_key()
    if not api_key_val:
        print(
            "  WARNING: no API key. Pass --api-key, set LORE_API_KEY, or run "
            "`lore serve` once to auto-bootstrap ~/.lore/key.txt."
        )

    # Write hook script
    script = CLAUDE_CODE_HOOK_SCRIPT.format(server_url=server_url, api_key=api_key_val)
    hook_path.write_text(script)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  Hook script: {hook_path}")

    # Update settings.json
    settings_path = _claude_settings_path()
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Add or update the hook entry (new format with matcher + hooks array)
    hooks = settings.setdefault("hooks", {})
    prompt_hooks = hooks.setdefault("UserPromptSubmit", [])

    hook_cmd = str(hook_path)

    # Check if already registered (idempotent) — check both old and new format
    already_exists = any(
        (isinstance(h, dict) and h.get("command") == hook_cmd)
        or (
            isinstance(h, dict)
            and any(
                inner.get("command") == hook_cmd
                for inner in h.get("hooks", [])
                if isinstance(inner, dict)
            )
        )
        for h in prompt_hooks
    )
    if not already_exists:
        prompt_hooks.append({
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": hook_cmd,
                }
            ],
        })

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  Settings:    {settings_path}")
    print("Claude Code hook installed successfully.")


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
    hook = _claude_hook_path()
    settings = _claude_settings_path()
    print("\nClaude Code:")
    print(f"  Hook:     {hook} {'[installed]' if hook.exists() else '[not installed]'}")
    if settings.exists():
        try:
            s = json.loads(settings.read_text())
            hooks = s.get("hooks", {}).get("UserPromptSubmit", [])
            hook_cmd = str(hook)
            registered = any(
                (isinstance(h, dict) and h.get("command") == hook_cmd)
                or (
                    isinstance(h, dict)
                    and any(
                        inner.get("command") == hook_cmd
                        for inner in h.get("hooks", [])
                        if isinstance(inner, dict)
                    )
                )
                for h in hooks
            )
            print(f"  Settings: {settings} {'[registered]' if registered else '[not registered]'}")
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
        hook = _claude_hook_path()
        if hook.exists():
            hook.unlink()
            print(f"  Removed hook: {hook}")

        # Remove from settings.json
        settings_path = _claude_settings_path()
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text())
                hooks = settings.get("hooks", {}).get("UserPromptSubmit", [])
                hook_cmd = str(hook)
                settings["hooks"]["UserPromptSubmit"] = [
                    h for h in hooks
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
