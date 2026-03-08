# Setting Up Lore with Warp

> **Note:** Warp does not currently support per-prompt hooks. Context injection uses a static `WARP.md` file (not per-prompt). For per-prompt memory injection, use a runtime with hook support ([Claude Code](setup-claude-code.md), [Cursor](setup-cursor.md), [Codex CLI](setup-codex.md)).

## Static Context via WARP.md

Warp reads a `WARP.md` file for agent context. You can export Lore memories into it periodically:

```bash
# Export top memories as a prompt file
lore prompt --format markdown > WARP.md
```

This gives the Warp agent access to your stored memories, but the context is static — it won't update per-prompt. Re-run the export when memories change.

## MCP Tools

MCP tools give your agent 20 tools (remember, recall, forget, etc.) for explicit memory operations.

Warp supports MCP servers. Add to your Warp MCP config:

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

1. Restart Warp after configuring MCP.
2. Ask: "Remember that our API uses REST with JSON responses"
3. Ask: "What do you know about our API?"
4. You should see Lore's recall tool being invoked.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| WARP.md not picked up | Ensure the file is in your project root. Restart Warp. |
| Tools not appearing | Verify MCP config is correct. Restart Warp. |
| "command not found: uvx" | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` or use `python -m lore.mcp.server` instead. |
| Memories not persisting | Check `LORE_PROJECT` is set. Memories are stored in `~/.lore/default.db` by default. |
| Stale context in WARP.md | Re-run `lore prompt --format markdown > WARP.md` to refresh. |
