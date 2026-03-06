# Setting Up Lore with Claude Desktop

## Prerequisites

- Python 3.9+
- Claude Desktop installed ([download](https://claude.ai/download))

## Configuration

Edit your Claude Desktop config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

### Local Mode (SQLite -- zero setup)

```json
{
  "mcpServers": {
    "lore": {
      "command": "uvx",
      "args": ["lore-memory"],
      "env": {
        "LORE_PROJECT": "my-project"
      }
    }
  }
}
```

### Remote Mode (self-hosted server)

```json
{
  "mcpServers": {
    "lore": {
      "command": "uvx",
      "args": ["lore-memory"],
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

## Verify It Works

1. Restart Claude Desktop after saving the config.
2. Ask: "Remember that our API uses REST with JSON responses"
3. Ask: "What do you know about our API?"
4. You should see Lore's `remember` and `recall` tools being invoked in the tool-use indicators.

## Enable LLM Features (Optional)

Add these to the `env` block for LLM-powered enrichment, classification, and fact extraction:

```json
{
  "mcpServers": {
    "lore": {
      "command": "uvx",
      "args": ["lore-memory"],
      "env": {
        "LORE_PROJECT": "my-project",
        "LORE_ENRICHMENT_ENABLED": "true",
        "LORE_LLM_PROVIDER": "anthropic",
        "LORE_LLM_MODEL": "claude-sonnet-4-20250514",
        "LORE_LLM_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Tools not appearing | Restart Claude Desktop. Check the config file is valid JSON. |
| "command not found: uvx" | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` or use `python -m lore.mcp.server` instead of `uvx lore-memory`. |
| "No module named lore" | Run `pip install lore-sdk` first, then use `python -m lore.mcp.server` as the command. |
| Memories not persisting | Check `LORE_PROJECT` is set. Memories are stored in `~/.lore/default.db` by default. |
| LLM enrichment not working | Verify `LORE_ENRICHMENT_ENABLED=true` and that your API key is valid. Run `pip install lore-sdk[enrichment]` for litellm support. |
