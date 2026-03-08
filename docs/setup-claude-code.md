# Setting Up Lore with Claude Code

## Auto-Retrieval (Recommended)

Auto-retrieval injects relevant memories into every prompt **before** the agent sees it. No tool calls needed — memories just appear in context.

### Option A: One-command setup (v0.9.0+)

```bash
lore setup claude-code
```

This creates the hook script and updates your settings automatically.

### Option B: Manual setup

#### 1. Create the hook script

Create `~/.claude/hooks/lore-retrieve.sh`:

```bash
#!/bin/bash
# Auto-inject Lore memories into every Claude Code prompt
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')

# Skip short/empty prompts
[ -z "$PROMPT" ] || [ ${#PROMPT} -lt 10 ] && exit 0

ENCODED=$(printf '%s' "$PROMPT" | jq -sRr @uri)
RESPONSE=$(curl -s --max-time 2 \
  "http://localhost:8765/v1/retrieve?query=${ENCODED}&limit=5&min_score=0.3&format=markdown" \
  -H "Authorization: Bearer ${LORE_API_KEY}" 2>/dev/null)

COUNT=$(echo "$RESPONSE" | jq -r '.count // 0' 2>/dev/null)
if [ "$COUNT" -gt 0 ]; then
  FORMATTED=$(echo "$RESPONSE" | jq -r '.formatted // empty' 2>/dev/null)
  jq -n --arg ctx "🧠 Relevant memories from Lore:
$FORMATTED" '{
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext: $ctx
    }
  }'
fi
```

```bash
chmod +x ~/.claude/hooks/lore-retrieve.sh
```

#### 2. Register the hook

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/lore-retrieve.sh"
          }
        ]
      }
    ]
  }
}
```

#### 3. Start the Lore server

```bash
pip install lore-sdk
lore serve  # starts on port 8765
```

### Verify Auto-Retrieval

1. Start Claude Code: `claude`
2. Store a memory: `curl -X POST http://localhost:8765/v1/memories -H "Authorization: Bearer $LORE_API_KEY" -d '{"content": "Our API rate limit is 100 req/min"}'`
3. Ask Claude: "What API rate limits should I use?"
4. You should see a `🧠 Relevant memories from Lore` block injected before the agent responds.

---

## MCP Tools (Manual)

MCP tools give your agent 20 tools (remember, recall, forget, etc.) for explicit memory operations. Use alongside auto-retrieval for storing new memories.

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
| Hook not firing | Verify `~/.claude/settings.json` has the `hooks` block. Check that `lore-retrieve.sh` is executable (`chmod +x`). |
| No memories injected | Ensure `lore serve` is running on port 8765. Check `curl http://localhost:8765/health`. |
| Tools not appearing | Restart Claude Code. Verify `.claude/settings.json` is in the project root and is valid JSON. |
| "command not found: uvx" | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` or use `python -m lore.mcp.server` instead. |
| "No module named lore" | Run `pip install lore-sdk` first, then use `python -m lore.mcp.server` as the command. |
| Memories not persisting | Check `LORE_PROJECT` is set. Memories are stored in `~/.lore/default.db` by default. |
| LLM enrichment not working | Verify `LORE_ENRICHMENT_ENABLED=true` and that your API key is valid. Run `pip install lore-sdk[enrichment]`. |
