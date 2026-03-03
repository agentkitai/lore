# Windsurf Setup Guide

Connect Lore to Windsurf (Codeium) for AI memory.

## Prerequisites

- [Windsurf](https://codeium.com/windsurf) installed
- Python 3.9+ with `pip install lore-sdk[mcp]`

## Setup

### Step 1: Install Lore

```bash
pip install lore-sdk[mcp]
```

### Step 2: Add MCP Config

Open Windsurf settings and add the MCP server configuration:

#### Local mode:

```json
{
  "mcpServers": {
    "lore": {
      "command": "python",
      "args": ["-m", "lore.mcp"],
      "env": {
        "LORE_PROJECT": "my-project"
      }
    }
  }
}
```

#### Remote mode:

```json
{
  "mcpServers": {
    "lore": {
      "command": "python",
      "args": ["-m", "lore.mcp"],
      "env": {
        "LORE_STORE": "remote",
        "LORE_API_URL": "http://localhost:8765",
        "LORE_API_KEY": "lore_sk_your_key_here"
      }
    }
  }
}
```

### Step 3: Restart Windsurf

Restart the editor. Lore's memory tools will be available in Cascade conversations.

## Troubleshooting

**MCP not connecting?**
- Verify `python -m lore.mcp` works in your terminal
- Check Windsurf's MCP settings panel for connection status
- Ensure Python path is correct in your configuration
