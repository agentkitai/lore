# Setting Up Lore with Windsurf

## Prerequisites

- Python 3.9+
- Windsurf IDE installed

## Configuration

Windsurf uses a global MCP config file at `~/.codeium/windsurf/mcp_config.json`.

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

1. Restart Windsurf after saving the config.
2. Open the Cascade panel.
3. Ask: "Remember that our API uses REST with JSON responses"
4. Ask: "What do you know about our API?"
5. You should see Lore's recall tool being invoked.

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
| Tools not appearing | Restart Windsurf. Verify the config file path is correct for your OS. |
| "command not found: uvx" | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` or use `python -m lore.mcp.server` instead. |
| "No module named lore" | Run `pip install lore-sdk` first, then use `python -m lore.mcp.server` as the command. |
| Config is global | Windsurf uses a single global config. Use `LORE_PROJECT` to scope memories per project. |
| LLM enrichment not working | Verify `LORE_ENRICHMENT_ENABLED=true` and that your API key is valid. Run `pip install lore-sdk[enrichment]`. |
