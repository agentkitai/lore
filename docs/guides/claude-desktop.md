# Claude Desktop Setup Guide

Connect Lore to Claude Desktop for persistent memory across conversations.

## Prerequisites

- [Claude Desktop](https://claude.ai/download) installed
- Python 3.9+ with `pip install lore-sdk[mcp]`

## Option 1: Local Mode (Zero Config)

Best for single-user setups. Memories stored in SQLite on your machine.

### Step 1: Install Lore

```bash
pip install lore-sdk[mcp]
```

### Step 2: Add MCP Config

Open Claude Desktop settings and edit the MCP configuration file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add this configuration:

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

### Step 3: Restart Claude Desktop

Restart the app. You should see the Lore tools (remember, recall, forget, list, stats) available.

### Step 4: Test It

Ask Claude: *"Remember that our API rate limit is 100 requests per minute"*

Then in a new conversation: *"What do we know about rate limits?"*

---

## Option 2: Remote Mode (Shared Server)

Best for teams or when you want a persistent server with PostgreSQL.

### Step 1: Start the Server

```bash
git clone https://github.com/amitpaz1/lore.git && cd lore
docker compose up -d
```

### Step 2: Get an API Key

```bash
curl -s -X POST http://localhost:8765/v1/org/init \
  -H "Content-Type: application/json" \
  -d '{"name": "my-org"}' | python3 -m json.tool
```

Save the `api_key` from the response.

### Step 3: Install Lore

```bash
pip install lore-sdk[mcp]
```

### Step 4: Add MCP Config

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

### Step 5: Restart Claude Desktop

---

## MCP Tools Available

Once connected, Claude has access to these tools:

| Tool | Description |
|------|-------------|
| `remember` | Store a memory with content, type, tags |
| `recall` | Semantic search over stored memories |
| `forget` | Delete memories by ID or filter |
| `list` | Browse memories with pagination |
| `stats` | View memory store statistics |

## Troubleshooting

**Tools not showing up?**
- Check that `python -m lore.mcp` runs without errors in your terminal
- Verify the config JSON is valid (no trailing commas)
- Restart Claude Desktop completely (quit and reopen)

**"Module not found" error?**
- Make sure you installed with MCP extras: `pip install lore-sdk[mcp]`
- Verify the `python` command in your config points to the right Python

**Using a virtual environment?**
- Use the full path to your venv's Python in the config:
  ```json
  "command": "/path/to/venv/bin/python"
  ```
