"""Generate integration config files for AI agent platforms.

Supports: claude-code, cursor, codex, openclaw.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# ── Template content ──────────────────────────────────────────────

CLAUDE_MD_CONTENT = """\
# Lore — Universal AI Memory

You have access to **Lore**, a persistent memory system via MCP tools.
Use it to remember context across sessions and recall relevant knowledge.

## When to Use Lore

### Session Start
Call `recent_activity(hours=24)` to load what happened recently.
This gives you grouped context from the last day — decisions, lessons, work done.

### During Work
- **Before debugging**: `recall("describe the problem")` — check if this was solved before
- **After solving something non-obvious**: `remember("what you learned", type="lesson")`
- **Key decisions**: `remember("decision and reasoning", type="note")`
- **Preferences discovered**: `remember("user prefers X", type="preference")`

### Pre-Compaction (Automatic)
Lore's session accumulator auto-saves snapshots when your context grows large.
You don't need to manually call `save_snapshot` — but you can if you want to
preserve specific state before a complex transition:
```
save_snapshot(content="Current state: ...", title="mid-refactor checkpoint")
```

## Key Tools

| Tool | When |
|------|------|
| `recall(query)` | Search memories semantically |
| `remember(content, type)` | Save a memory (types: note, lesson, fact, preference, pattern, convention) |
| `recent_activity(hours)` | Load recent session context |
| `save_snapshot(content)` | Manually checkpoint current state |
| `suggest(context)` | Get proactive memory recommendations based on session context |
| `topics()` | Browse auto-generated topic summaries |
| `graph_query(query)` | Explore knowledge graph connections |
| `entity_map(name)` | Find everything related to an entity |
| `review_digest()` | Review pending graph connections (with risk scores) |
| `on_this_day()` | Memories from this date in prior years |
| `export(format)` | Export all data (json/markdown) |

## Types for `remember`

- `lesson` — Bug fixes, gotchas, things learned the hard way
- `fact` — Objective information (API endpoints, config values, specs)
- `preference` — User preferences, style choices
- `pattern` — Recurring patterns or anti-patterns
- `convention` — Project conventions, naming rules
- `note` — General notes, decisions, context

## Don't Overthink It

If something seems worth remembering, `remember` it. If you're stuck, `recall` it.
The system handles deduplication, scoring, and cleanup automatically.
"""


def _claude_mcp_settings(server_url: str, api_key: Optional[str]) -> dict:
    """Build .claude/settings.json MCP server config."""
    env: dict = {"LORE_PROJECT": os.path.basename(os.getcwd())}
    if server_url != "http://localhost:8765":
        env["LORE_STORE"] = "remote"
        env["LORE_API_URL"] = server_url
    if api_key:
        env["LORE_API_KEY"] = api_key
    return {
        "mcpServers": {
            "lore": {
                "command": "uvx",
                "args": ["lore-memory"],
                "env": env,
            }
        }
    }


CURSOR_RULES_CONTENT = """\
# Lore Integration

This project uses Lore for persistent AI memory across sessions.

## Memory Tools (via MCP)

You have access to Lore MCP tools. Use them as follows:

### Session Start
- Call `recent_activity(hours=24)` to load recent context.

### During Work
- Before debugging, call `recall("describe the problem")` to check for prior solutions.
- After solving something non-obvious, call `remember("what you learned", type="lesson")`.
- For key decisions, call `remember("decision and reasoning", type="note")`.
- For discovered preferences, call `remember("user prefers X", type="preference")`.

### Available Tools
- `recall(query)` — Search memories semantically
- `remember(content, type)` — Save a memory (types: note, lesson, fact, preference, pattern, convention)
- `recent_activity(hours)` — Load recent session context
- `save_snapshot(content)` — Checkpoint current state
- `suggest(context)` — Get proactive memory recommendations
- `topics()` — Browse topic summaries
- `graph_query(query)` — Explore knowledge graph connections
- `entity_map(name)` — Find everything related to an entity

### Memory Types
- `lesson` — Bug fixes, gotchas, things learned the hard way
- `fact` — Objective information (API endpoints, config values, specs)
- `preference` — User preferences, style choices
- `pattern` — Recurring patterns or anti-patterns
- `convention` — Project conventions, naming rules
- `note` — General notes, decisions, context

If something seems worth remembering, `remember` it. If you're stuck, `recall` it.
"""


def _cursor_mcp_config(server_url: str, api_key: Optional[str]) -> dict:
    """Build .cursor/mcp.json config."""
    env: dict = {"LORE_PROJECT": os.path.basename(os.getcwd())}
    if server_url != "http://localhost:8765":
        env["LORE_STORE"] = "remote"
        env["LORE_API_URL"] = server_url
    if api_key:
        env["LORE_API_KEY"] = api_key
    return {
        "mcpServers": {
            "lore": {
                "command": "uvx",
                "args": ["lore-memory"],
                "env": env,
            }
        }
    }


CODEX_YAML_TEMPLATE = """\
# Lore MCP integration for Codex CLI
# Generated by: lore integrate --platform codex

mcpServers:
  lore:
    command: uvx
    args:
      - lore-memory
    env:
      LORE_PROJECT: {project}
{remote_env}
"""

OPENCLAW_CONFIG_TEMPLATE = """\
{{
  "mcpServers": {{
    "lore": {{
      "command": "uvx",
      "args": ["lore-memory"],
      "env": {{
        "LORE_PROJECT": "{project}"{remote_env}
      }}
    }}
  }}
}}
"""


# ── Platform generators ───────────────────────────────────────────


def _generate_claude_code(server_url: str, api_key: Optional[str]) -> None:
    """Generate CLAUDE.md and .claude/settings.json for Claude Code."""
    cwd = Path.cwd()
    files_written = []

    # 1. Generate/update CLAUDE.md
    claude_md_path = cwd / "CLAUDE.md"
    if claude_md_path.exists():
        existing = claude_md_path.read_text()
        if "Lore" in existing:
            print(f"  CLAUDE.md already contains Lore instructions — skipped.")
        else:
            # Append Lore section
            with open(claude_md_path, "a") as f:
                f.write("\n\n" + CLAUDE_MD_CONTENT)
            files_written.append(str(claude_md_path))
            print(f"  Updated: {claude_md_path} (appended Lore instructions)")
    else:
        claude_md_path.write_text(CLAUDE_MD_CONTENT)
        files_written.append(str(claude_md_path))
        print(f"  Created: {claude_md_path}")

    # 2. Generate/update .claude/settings.json with MCP server config
    settings_dir = cwd / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_path = settings_dir / "settings.json"

    new_config = _claude_mcp_settings(server_url, api_key)

    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}
        # Merge mcpServers
        mcp = existing.setdefault("mcpServers", {})
        mcp["lore"] = new_config["mcpServers"]["lore"]
        settings_path.write_text(json.dumps(existing, indent=2) + "\n")
        print(f"  Updated: {settings_path} (added/updated lore MCP server)")
    else:
        settings_path.write_text(json.dumps(new_config, indent=2) + "\n")
        print(f"  Created: {settings_path}")
    files_written.append(str(settings_path))

    print()
    print("Claude Code integration complete.")
    print("  1. Start the Lore server: lore serve")
    print("  2. Open Claude Code in this directory: claude")
    print("  3. Lore MCP tools will be available automatically.")


def _generate_cursor(server_url: str, api_key: Optional[str]) -> None:
    """Generate .cursorrules and .cursor/mcp.json for Cursor."""
    cwd = Path.cwd()

    # 1. Generate .cursorrules
    rules_path = cwd / ".cursorrules"
    if rules_path.exists():
        existing = rules_path.read_text()
        if "Lore" in existing:
            print(f"  .cursorrules already contains Lore instructions — skipped.")
        else:
            with open(rules_path, "a") as f:
                f.write("\n\n" + CURSOR_RULES_CONTENT)
            print(f"  Updated: {rules_path} (appended Lore instructions)")
    else:
        rules_path.write_text(CURSOR_RULES_CONTENT)
        print(f"  Created: {rules_path}")

    # 2. Generate .cursor/mcp.json
    cursor_dir = cwd / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    mcp_path = cursor_dir / "mcp.json"

    new_config = _cursor_mcp_config(server_url, api_key)

    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}
        mcp = existing.setdefault("mcpServers", {})
        mcp["lore"] = new_config["mcpServers"]["lore"]
        mcp_path.write_text(json.dumps(existing, indent=2) + "\n")
        print(f"  Updated: {mcp_path} (added/updated lore MCP server)")
    else:
        mcp_path.write_text(json.dumps(new_config, indent=2) + "\n")
        print(f"  Created: {mcp_path}")

    print()
    print("Cursor integration complete.")
    print("  1. Start the Lore server: lore serve")
    print("  2. Restart Cursor to pick up the new config.")
    print("  3. Lore MCP tools will be available in Composer (Cmd+I / Ctrl+I).")


def _generate_codex(server_url: str, api_key: Optional[str]) -> None:
    """Generate codex.yaml with Lore MCP config for Codex CLI."""
    cwd = Path.cwd()
    config_path = cwd / "codex.yaml"

    project = os.path.basename(os.getcwd())
    remote_env = ""
    if server_url != "http://localhost:8765":
        remote_env += f"      LORE_STORE: remote\n      LORE_API_URL: {server_url}\n"
    if api_key:
        remote_env += f"      LORE_API_KEY: {api_key}\n"

    content = CODEX_YAML_TEMPLATE.format(project=project, remote_env=remote_env)

    if config_path.exists():
        existing = config_path.read_text()
        if "lore" in existing.lower():
            print(f"  codex.yaml already contains Lore config — skipped.")
        else:
            with open(config_path, "a") as f:
                f.write("\n" + content)
            print(f"  Updated: {config_path} (appended Lore MCP config)")
    else:
        config_path.write_text(content)
        print(f"  Created: {config_path}")

    print()
    print("Codex CLI integration complete.")
    print("  1. Start the Lore server: lore serve")
    print("  2. Run Codex in this directory — Lore MCP tools will be available.")


def _generate_openclaw(server_url: str, api_key: Optional[str]) -> None:
    """Generate OpenClaw MCP config."""
    cwd = Path.cwd()

    project = os.path.basename(os.getcwd())
    remote_parts = []
    if server_url != "http://localhost:8765":
        remote_parts.append(f',\n        "LORE_STORE": "remote"')
        remote_parts.append(f',\n        "LORE_API_URL": "{server_url}"')
    if api_key:
        remote_parts.append(f',\n        "LORE_API_KEY": "{api_key}"')
    remote_env = "".join(remote_parts)

    content = OPENCLAW_CONFIG_TEMPLATE.format(project=project, remote_env=remote_env)

    # OpenClaw uses a config directory
    config_dir = Path.home() / ".openclaw"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "mcp.json"

    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}
        mcp = existing.setdefault("mcpServers", {})
        env: dict = {"LORE_PROJECT": project}
        if server_url != "http://localhost:8765":
            env["LORE_STORE"] = "remote"
            env["LORE_API_URL"] = server_url
        if api_key:
            env["LORE_API_KEY"] = api_key
        mcp["lore"] = {
            "command": "uvx",
            "args": ["lore-memory"],
            "env": env,
        }
        config_path.write_text(json.dumps(existing, indent=2) + "\n")
        print(f"  Updated: {config_path} (added/updated lore MCP server)")
    else:
        # Write the formatted template
        parsed = json.loads(content)
        config_path.write_text(json.dumps(parsed, indent=2) + "\n")
        print(f"  Created: {config_path}")

    print()
    print("OpenClaw integration complete.")
    print("  1. Start the Lore server: lore serve")
    print("  2. Restart OpenClaw — Lore MCP tools will be available.")


# ── Public entry point ────────────────────────────────────────────


def generate_integration(
    platform: str,
    server_url: str = "http://localhost:8765",
    api_key: Optional[str] = None,
) -> None:
    """Generate integration config files for the given platform."""
    api_key = api_key or os.environ.get("LORE_API_KEY")

    print(f"Generating Lore integration for: {platform}")
    print(f"  Server URL: {server_url}")
    print(f"  API Key:    {'set' if api_key else 'not set'}")
    print()

    generators = {
        "claude-code": _generate_claude_code,
        "cursor": _generate_cursor,
        "codex": _generate_codex,
        "openclaw": _generate_openclaw,
    }

    gen = generators.get(platform)
    if not gen:
        print(f"Unknown platform: {platform}", file=sys.stderr)
        print(f"Supported: {', '.join(sorted(generators))}", file=sys.stderr)
        sys.exit(1)

    gen(server_url, api_key)
