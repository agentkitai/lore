# Cursor IDE Setup Guide

Connect Lore to Cursor for project-specific AI memory.

## Prerequisites

- [Cursor](https://cursor.sh) installed
- Python 3.9+ with `pip install lore-sdk[mcp]`

## Setup

### Step 1: Install Lore

```bash
pip install lore-sdk[mcp]
```

### Step 2: Create MCP Config

Create `.cursor/mcp.json` in your project root:

#### Local mode (single user, zero config):

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

#### Remote mode (shared server):

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

### Step 3: Restart Cursor

Restart the editor or reload the window. The Lore MCP tools will be available in the AI chat.

## Per-Project Memory

Set `LORE_PROJECT` to your project name to isolate memories per project:

```json
{
  "mcpServers": {
    "lore": {
      "command": "python",
      "args": ["-m", "lore.mcp"],
      "env": {
        "LORE_PROJECT": "backend-api"
      }
    }
  }
}
```

Different projects get different memory scopes. Memories from `backend-api` won't show up in `frontend-app` queries.

## Troubleshooting

**MCP tools not appearing?**
- Check Cursor settings > MCP to verify the server is connected
- Run `python -m lore.mcp` in your terminal to test
- Ensure the `.cursor/mcp.json` file is at the project root

**Using a virtual environment?**
- Use the full path to your venv's Python:
  ```json
  "command": "/path/to/venv/bin/python"
  ```
