# Setting Up Lore with OpenClaw

## Auto-Retrieval (Recommended)

Auto-retrieval injects relevant memories into every prompt **before** the agent sees it. No tool calls needed — memories just appear in context.

### Option A: One-command setup (v0.9.0+)

```bash
lore setup openclaw
```

This creates the hook handler and `HOOK.md` automatically.

### Option B: Manual setup

#### 1. Create the hook directory

```bash
mkdir -p ~/.openclaw/hooks/lore-retrieve
```

#### 2. Create HOOK.md

Create `~/.openclaw/hooks/lore-retrieve/HOOK.md`:

```markdown
---
name: lore-retrieve
event: message:preprocessed
description: Auto-inject relevant Lore memories into agent context
---
```

#### 3. Create the handler

Create `~/.openclaw/hooks/lore-retrieve/handler.ts`:

```typescript
import type { HookContext } from "@openclaw/types";

export default async function handler(ctx: HookContext) {
  const prompt = ctx.message?.content;
  if (!prompt || prompt.length < 10) return;

  const encoded = encodeURIComponent(prompt);
  const apiKey = process.env.LORE_API_KEY || "";

  try {
    const res = await fetch(
      `http://localhost:8765/v1/retrieve?query=${encoded}&limit=5&min_score=0.3&format=markdown`,
      {
        headers: { Authorization: `Bearer ${apiKey}` },
        signal: AbortSignal.timeout(2000),
      }
    );

    const data = await res.json();
    if (data.count > 0 && data.formatted) {
      ctx.addContext(`🧠 Relevant memories from Lore:\n${data.formatted}`);
    }
  } catch {
    // Fail-open: if Lore is slow or down, the agent responds normally
  }
}
```

#### 4. Start the Lore server

```bash
pip install lore-sdk
lore serve  # starts on port 8765
```

### Verify Auto-Retrieval

1. Start OpenClaw.
2. Store a memory: `curl -X POST http://localhost:8765/v1/memories -H "Authorization: Bearer $LORE_API_KEY" -d '{"content": "Our API rate limit is 100 req/min"}'`
3. Ask a related question.
4. You should see a `🧠 Relevant memories from Lore` block injected into the agent's context.

---

## MCP Tools (Manual)

MCP tools give your agent 20 tools (remember, recall, forget, etc.) for explicit memory operations. Use alongside auto-retrieval for storing new memories.

OpenClaw supports MCP servers via mcporter. Add to your OpenClaw config:

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

1. Start OpenClaw.
2. Ask: "Remember that our API uses REST with JSON responses"
3. Ask: "What do you know about our API?"
4. You should see Lore's `remember` and `recall` tools being invoked.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Hook not firing | Verify `~/.openclaw/hooks/lore-retrieve/HOOK.md` has `event: message:preprocessed`. Restart OpenClaw. |
| No memories injected | Ensure `lore serve` is running on port 8765. Check `curl http://localhost:8765/health`. |
| Handler TypeScript errors | Ensure handler.ts exports a default async function. Check OpenClaw logs for details. |
| Tools not appearing | Verify MCP config is correct. Restart OpenClaw. |
| "command not found: uvx" | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` or use `python -m lore.mcp.server` instead. |
| Memories not persisting | Check `LORE_PROJECT` is set. Memories are stored in `~/.lore/default.db` by default. |
