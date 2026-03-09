# Setting Up Lore with Codex CLI

## Auto-Retrieval (Recommended)

Auto-retrieval injects relevant memories into every prompt **before** the agent sees it. No tool calls needed — memories just appear in context.

### 1. Create the hook script

Create `~/.codex/hooks/lore-retrieve.sh`:

```bash
#!/bin/bash
# Auto-inject Lore memories into every Codex CLI prompt
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // .user_message // empty')

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
chmod +x ~/.codex/hooks/lore-retrieve.sh
```

### 2. Register the hook

Add to `codex.yaml` in your project root:

```yaml
hooks:
  beforePlan:
    command: ~/.codex/hooks/lore-retrieve.sh
```

### 3. Start the Lore server

```bash
pip install lore-sdk
lore serve  # starts on port 8765
```

> **Automated setup:** `lore setup codex [--server-url URL] [--api-key KEY]` will perform steps 1–2 automatically.

### Verify Auto-Retrieval

1. Store a memory via the API or CLI.
2. Run Codex and ask a related question.
3. Relevant memories should appear in the agent's context automatically.

---

## MCP Tools (Manual)

MCP tools give your agent 20 tools (remember, recall, forget, etc.) for explicit memory operations. Use alongside auto-retrieval for storing new memories.

Add to `codex.yaml` in your project root:

```yaml
mcpServers:
  lore:
    command: uvx
    args:
      - lore-memory
    env:
      LORE_PROJECT: my-project
```

### Remote Mode (self-hosted server)

```yaml
mcpServers:
  lore:
    command: uvx
    args:
      - lore-memory
    env:
      LORE_STORE: remote
      LORE_API_URL: http://localhost:8765
      LORE_API_KEY: lore_sk_your_key_here
      LORE_PROJECT: my-project
```

## Verify It Works

1. Run Codex in your project directory.
2. Ask: "Remember that our API uses REST with JSON responses"
3. Ask: "What do you know about our API?"
4. You should see Lore's recall tool being invoked.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Hook not firing | Verify `codex.yaml` has the `hooks.beforePlan` entry. Check that the script is executable (`chmod +x`). |
| No memories injected | Ensure `lore serve` is running on port 8765. Check `curl http://localhost:8765/health`. |
| Tools not appearing | Verify `codex.yaml` has the `mcpServers` section. Restart Codex. |
| "command not found: uvx" | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` or use `python -m lore.mcp.server` instead. |
| Memories not persisting | Check `LORE_PROJECT` is set. Memories are stored in `~/.lore/default.db` by default. |
