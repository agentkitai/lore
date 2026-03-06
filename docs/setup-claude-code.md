# Setting Up Lore with Claude Code

## Prerequisites

- Python 3.9+
- Claude Code CLI installed (`npm install -g @anthropic-ai/claude-code`)

## Configuration

There are two ways to configure Lore with Claude Code: project settings or CLAUDE.md.

### Method 1: Project Settings (recommended)

Create `.claude/settings.json` in your project root:

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

### Method 2: CLAUDE.md

Add to your project's `CLAUDE.md` file:

```markdown
## MCP Servers

This project uses Lore for persistent memory. The MCP server is configured in `.claude/settings.json`.
When solving problems, always check Lore first with `recall`. After solving tricky bugs, save lessons with `remember`.
```

### Remote Mode (self-hosted server)

For either method, use these env vars to connect to a remote Lore server:

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

1. Start Claude Code in your project directory: `claude`
2. Ask: "Remember that our API uses REST with JSON responses"
3. Ask: "What do you know about our API?"
4. You should see Lore's `remember` and `recall` tools being invoked.

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
| Tools not appearing | Restart Claude Code. Verify `.claude/settings.json` is in the project root and is valid JSON. |
| "command not found: uvx" | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` or use `python -m lore.mcp.server` instead. |
| "No module named lore" | Run `pip install lore-sdk` first, then use `python -m lore.mcp.server` as the command. |
| Memories not persisting | Check `LORE_PROJECT` is set. Memories are stored in `~/.lore/default.db` by default. |
| LLM enrichment not working | Verify `LORE_ENRICHMENT_ENABLED=true` and that your API key is valid. Run `pip install lore-sdk[enrichment]`. |
