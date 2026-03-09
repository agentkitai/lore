# Setting Up Lore with Cursor

## Auto-Retrieval (Recommended)

Auto-retrieval injects relevant memories into every prompt **before** the agent sees it. No tool calls needed — memories just appear in context.

### 1. Create the hook script

Create `.cursor/hooks/lore-retrieve.sh` in your project root:

```bash
#!/bin/bash
# Auto-inject Lore memories into every Cursor prompt
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.user_message // .prompt // empty')

# Skip short/empty prompts
[ -z "$PROMPT" ] || [ ${#PROMPT} -lt 10 ] && exit 0

ENCODED=$(printf '%s' "$PROMPT" | jq -sRr @uri)
RESPONSE=$(curl -s --max-time 2 \
  "http://localhost:8765/v1/retrieve?query=${ENCODED}&limit=5&min_score=0.3&format=markdown" \
  -H "Authorization: Bearer ${LORE_API_KEY}" 2>/dev/null)

COUNT=$(echo "$RESPONSE" | jq -r '.count // 0' 2>/dev/null)
if [ "$COUNT" -gt 0 ]; then
  FORMATTED=$(echo "$RESPONSE" | jq -r '.formatted // empty' 2>/dev/null)
  echo "$FORMATTED"
fi
```

```bash
chmod +x .cursor/hooks/lore-retrieve.sh
```

### 2. Register the hook

Cursor supports `beforeSubmitPrompt` hooks. Add to `.cursor/hooks/config.json`:

```json
{
  "hooks": {
    "beforeSubmitPrompt": [
      {
        "command": ".cursor/hooks/lore-retrieve.sh"
      }
    ]
  }
}
```

### 3. Start the Lore server

```bash
pip install lore-sdk
lore serve  # starts on port 8765
```

> **Automated setup:** `lore setup cursor [--server-url URL] [--api-key KEY]` will perform steps 1–2 automatically.

### Verify Auto-Retrieval

1. Restart Cursor after saving the config.
2. Store a memory via the API or MCP tools.
3. Open the Composer (Cmd+I / Ctrl+I) and ask a related question.
4. Relevant memories should appear in the agent's context automatically.

---

## MCP Tools (Manual)

MCP tools give your agent 20 tools (remember, recall, forget, etc.) for explicit memory operations. Use alongside auto-retrieval for storing new memories.

Create `.cursor/mcp.json` in your project root:

### Local Mode (SQLite — zero setup)

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

1. Restart Cursor after saving the config.
2. Open the Composer (Cmd+I / Ctrl+I).
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
| Hook not firing | Verify `.cursor/hooks/config.json` exists and the script is executable. Restart Cursor. |
| No memories injected | Ensure `lore serve` is running on port 8765. Check `curl http://localhost:8765/health`. |
| Tools not appearing | Restart Cursor. Ensure `.cursor/mcp.json` is in the project root. |
| "command not found: uvx" | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` or use `python -m lore.mcp.server` instead. |
| "No module named lore" | Run `pip install lore-sdk` first, then use `python -m lore.mcp.server` as the command. |
| Memories not persisting | Check `LORE_PROJECT` is set consistently across projects. |
| LLM enrichment not working | Verify `LORE_ENRICHMENT_ENABLED=true` and that your API key is valid. Run `pip install lore-sdk[enrichment]`. |
