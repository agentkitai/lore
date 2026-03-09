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

# Read the user's message from stdin
INPUT=$(cat)

# Claude Code sends "prompt" in hook input JSON
USER_MSG=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('prompt', '') or d.get('user_message', ''))
" 2>/dev/null || echo "")

if [ -z "$USER_MSG" ] || [ "${{#USER_MSG}}" -lt 10 ]; then
    exit 0
fi

# Query Lore for relevant memories
ENCODED=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$USER_MSG" 2>/dev/null || exit 0)
RESULT=$(curl -sf -H "Authorization: Bearer $LORE_KEY" \\
    "$LORE_SERVER_URL/v1/retrieve?query=$ENCODED&format=markdown&limit=5&min_score=0.3" 2>/dev/null || exit 0)

FORMATTED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('formatted',''))" 2>/dev/null || echo "")

if [ -n "$FORMATTED" ]; then
    # Escape for JSON embedding
    escape_for_json() {{
        local s="$1"
        s="${{s//\\\\/\\\\\\\\}}"
        s="${{s//\\"/\\\\\\"}}"
        s="${{s//\\$'\\n'/\\\\n}}"
        s="${{s//\\$'\\r'/\\\\r}}"
        s="${{s//\\$'\\t'/\\\\t}}"
        printf '%s' "$s"
    }}

    ESCAPED=$(escape_for_json "$FORMATTED")

    # Return JSON with hookSpecificOutput for Claude Code context injection
    cat <<EOF
{{
  "hookSpecificOutput": {{
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "${{ESCAPED}}"
  }}
}}
EOF
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
        echo "$FORMATTED"
    fi
else
    FORMATTED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('formatted',''))" 2>/dev/null || echo "")
    if [ -n "$FORMATTED" ]; then
        echo "$FORMATTED"
    fi
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
        echo "$FORMATTED"
    fi
else
    FORMATTED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('formatted',''))" 2>/dev/null || echo "")
    if [ -n "$FORMATTED" ]; then
        echo "$FORMATTED"
    fi
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
    api_key_val = api_key or os.environ.get("LORE_API_KEY", "")

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
    api_key_val = api_key or os.environ.get("LORE_API_KEY", "")

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
    api_key_val = api_key or os.environ.get("LORE_API_KEY", "")

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
