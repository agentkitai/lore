"""Lore setup — install auto-retrieval hooks for supported runtimes."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

# ── Hook script templates ──────────────────────────────────────────

CLAUDE_CODE_HOOK_SCRIPT = """\
#!/usr/bin/env bash
# Lore auto-retrieval hook for Claude Code
# Installed by: lore setup claude-code

set -euo pipefail

LORE_SERVER_URL="${{LORE_API_URL:-{server_url}}}"
LORE_KEY="${{LORE_API_KEY:-{api_key}}}"

# Read the user's message from stdin (hook receives JSON with user_message)
INPUT=$(cat)
USER_MSG=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('user_message',''))" 2>/dev/null || echo "")

if [ -z "$USER_MSG" ] || [ "${{#USER_MSG}}" -lt 10 ]; then
    exit 0
fi

# Query Lore for relevant memories
ENCODED=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$USER_MSG" 2>/dev/null || exit 0)
RESULT=$(curl -sf -H "Authorization: Bearer $LORE_KEY" \\
    "$LORE_SERVER_URL/v1/retrieve?query=$ENCODED&format=markdown&limit=5&min_score=0.3" 2>/dev/null || exit 0)

FORMATTED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('formatted',''))" 2>/dev/null || echo "")

if [ -n "$FORMATTED" ]; then
    echo "$FORMATTED"
fi
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

ENCODED=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$USER_MSG" 2>/dev/null || exit 0)
RESULT=$(curl -sf -H "Authorization: Bearer $LORE_KEY" \\
    "$LORE_SERVER_URL/v1/retrieve?query=$ENCODED&format=markdown&limit=5&min_score=0.3" 2>/dev/null || exit 0)

FORMATTED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('formatted',''))" 2>/dev/null || echo "")

if [ -n "$FORMATTED" ]; then
    echo "$FORMATTED"
fi
"""


# ── Paths ──────────────────────────────────────────────────────────

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


# ── Setup functions ────────────────────────────────────────────────


def setup_claude_code(server_url: str = "http://localhost:8765", api_key: str | None = None) -> None:
    """Install Lore auto-retrieval hook for Claude Code."""
    hooks_dir = _claude_hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_path = _claude_hook_path()
    api_key_val = api_key or os.environ.get("LORE_API_KEY", "")

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

    # Add or update the hook entry
    hooks = settings.setdefault("hooks", {})
    prompt_hooks = hooks.setdefault("UserPromptSubmit", [])

    # Check if already registered (idempotent)
    hook_entry = {
        "type": "command",
        "command": str(hook_path),
    }
    already_exists = any(
        h.get("command") == str(hook_path)
        for h in prompt_hooks
        if isinstance(h, dict)
    )
    if not already_exists:
        prompt_hooks.append(hook_entry)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  Settings:    {settings_path}")
    print("Claude Code hook installed successfully.")


def setup_openclaw(server_url: str = "http://localhost:8765", api_key: str | None = None) -> None:
    """Install Lore auto-retrieval hook for OpenClaw."""
    hooks_dir = _openclaw_hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_path = _openclaw_hook_path()
    api_key_val = api_key or os.environ.get("LORE_API_KEY", "")

    script = OPENCLAW_HOOK_SCRIPT.format(server_url=server_url, api_key=api_key_val)
    hook_path.write_text(script)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  Hook script: {hook_path}")
    print("OpenClaw hook installed successfully.")


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
            registered = any(
                h.get("command") == str(hook)
                for h in hooks
                if isinstance(h, dict)
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
                settings["hooks"]["UserPromptSubmit"] = [
                    h for h in hooks
                    if not (isinstance(h, dict) and h.get("command") == str(hook))
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
    else:
        print(f"Unknown runtime: {runtime}", file=sys.stderr)
        sys.exit(1)
