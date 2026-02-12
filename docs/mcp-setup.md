# MCP Setup Guide

Lore provides an MCP (Model Context Protocol) server that gives Claude and other AI assistants access to your lesson memory.

## Tools Provided

| Tool | Description |
|------|-------------|
| `save_lesson` | Save a lesson learned from solving a problem |
| `recall_lessons` | Search for relevant lessons |
| `upvote_lesson` | Boost a helpful lesson's ranking |
| `downvote_lesson` | Lower an unhelpful lesson's ranking |

## Install

```bash
pip install lore-sdk[mcp]
```

## Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

### Local Mode (SQLite)

```json
{
  "mcpServers": {
    "lore": {
      "command": "python",
      "args": ["-m", "lore.mcp.server"],
      "env": {
        "LORE_PROJECT": "my-project"
      }
    }
  }
}
```

### Remote Mode (Lore Cloud)

```json
{
  "mcpServers": {
    "lore": {
      "command": "python",
      "args": ["-m", "lore.mcp.server"],
      "env": {
        "LORE_STORE": "remote",
        "LORE_API_URL": "http://localhost:8765",
        "LORE_API_KEY": "lore_sk_your_key_here",
        "LORE_PROJECT": "my-project"
      }
    }
  }
}
```

## OpenClaw

Add to your OpenClaw skills config:

```yaml
skills:
  lore:
    type: mcp
    command: python
    args: ["-m", "lore.mcp.server"]
    env:
      LORE_STORE: remote
      LORE_API_URL: http://localhost:8765
      LORE_API_KEY: lore_sk_your_key_here
      LORE_PROJECT: my-project
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_STORE` | `local` | `local` (SQLite) or `remote` (Lore Cloud) |
| `LORE_PROJECT` | none | Default project scope for all operations |
| `LORE_API_URL` | — | Server URL (required for remote) |
| `LORE_API_KEY` | — | API key (required for remote) |

## Verify It Works

After configuring, ask Claude:

> "Search lore for lessons about rate limiting"

Claude should call the `recall_lessons` tool automatically.
