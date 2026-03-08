# MCP Setup Guide

> **Recommended:** For the best experience, use **auto-retrieval hooks** instead of (or alongside) MCP tools. Auto-retrieval injects relevant memories into every prompt automatically — no agent cooperation needed.
>
> Runtimes with hook support: [Claude Code](setup-claude-code.md) · [OpenClaw](setup-openclaw.md) · [Cursor](setup-cursor.md) · [Codex CLI](setup-codex.md)

Lore provides an MCP (Model Context Protocol) server that gives AI assistants access to persistent memory. This page is an index of per-client setup guides and common configuration.

## Client Setup Guides

| Client | Auto-Retrieval | Config Location | Guide |
|--------|---------------|----------------|-------|
| Claude Code | ✅ Hooks | `.claude/settings.json` in project root | [Setup guide](setup-claude-code.md) |
| OpenClaw | ✅ Hooks | `~/.openclaw/hooks/` | [Setup guide](setup-openclaw.md) |
| Cursor | ✅ Hooks | `.cursor/mcp.json` in project root | [Setup guide](setup-cursor.md) |
| Codex CLI | ✅ Hooks | `codex.yaml` in project root | [Setup guide](setup-codex.md) |
| Claude Desktop | MCP only | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) | [Setup guide](setup-claude-desktop.md) |
| VS Code / Copilot | MCP only | `.vscode/mcp.json` in project root | [Setup guide](setup-vscode.md) |
| Windsurf | MCP only | `~/.codeium/windsurf/mcp_config.json` (global) | [Setup guide](setup-windsurf.md) |
| Cline | MCP only | `.cline/mcp_settings.json` in project root | [Setup guide](setup-cline.md) |
| Warp | MCP only | Warp MCP config | [Setup guide](setup-warp.md) |
| ChatGPT | MCP bridge | Via MCP bridge (experimental) | [Setup guide](setup-chatgpt.md) |

## Minimal Config

Every client uses the same JSON structure. The minimal config for local mode:

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

Alternative command (if uvx is not installed):

```json
{
  "command": "python",
  "args": ["-m", "lore.mcp.server"]
}
```

## Tools Provided (20)

| Tool | Description |
|------|-------------|
| `remember` | Save a memory with optional tier, tags, and metadata |
| `recall` | Semantic search for relevant memories |
| `forget` | Delete a memory by ID |
| `list_memories` | List stored memories with filters |
| `stats` | Memory statistics (counts, tiers, importance) |
| `upvote_memory` | Boost a helpful memory's ranking |
| `downvote_memory` | Lower an unhelpful memory's ranking |
| `extract_facts` | Extract structured facts from text (requires LLM) |
| `list_facts` | List active facts from the knowledge base |
| `conflicts` | Show fact conflict log |
| `graph_query` | Traverse the knowledge graph from an entity |
| `entity_map` | List entities in the knowledge graph |
| `related` | Find related memories and entities |
| `classify` | Classify text by intent, domain, and emotion |
| `enrich` | Add LLM-extracted metadata to memories |
| `consolidate` | Merge duplicates and summarize memory clusters |
| `ingest` | Import content with source tracking |
| `as_prompt` | Export memories formatted for LLM prompts |
| `check_freshness` | Check memory freshness against git history |
| `github_sync` | Sync GitHub repo data into Lore |

See the [API Reference](api-reference.md) for detailed documentation of each tool.

## Environment Variables

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_STORE` | `local` | Storage backend: `local` (SQLite) or `remote` (HTTP) |
| `LORE_PROJECT` | none | Default project scope for all operations |
| `LORE_API_URL` | none | Server URL (required when `LORE_STORE=remote`) |
| `LORE_API_KEY` | none | API key (required when `LORE_STORE=remote`) |

### LLM Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_LLM_PROVIDER` | none | LLM provider: `anthropic`, `openai`, `azure`, etc. (via litellm) |
| `LORE_LLM_MODEL` | `gpt-4o-mini` | Model name for classification, fact extraction, consolidation |
| `LORE_LLM_API_KEY` | none | API key for the LLM provider |
| `LORE_LLM_BASE_URL` | none | Custom base URL for the LLM API (e.g., for Azure or local models) |

### Feature Toggles

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_ENRICHMENT_ENABLED` | `false` | Enable LLM enrichment (topics, sentiment, entities, categories) |
| `LORE_ENRICHMENT_MODEL` | `gpt-4o-mini` | Model for enrichment (can differ from main LLM model) |
| `LORE_CLASSIFY` | `false` | Enable dialog classification on remember |
| `LORE_KNOWLEDGE_GRAPH` | `false` | Enable knowledge graph entity/relationship extraction |
| `LORE_FACT_EXTRACTION` | `false` | Enable structured fact extraction from memories |

### Knowledge Graph Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_GRAPH_DEPTH` | `0` | Default graph traversal depth during recall (0 = disabled) |
| `LORE_GRAPH_CONFIDENCE_THRESHOLD` | `0.5` | Minimum confidence for graph entities |
| `LORE_GRAPH_CO_OCCURRENCE` | `true` | Extract co-occurrence relationships between entities |
| `LORE_GRAPH_CO_OCCURRENCE_WEIGHT` | `0.3` | Weight for co-occurrence edges |

## Remote Mode

To connect to a self-hosted Lore server instead of using local SQLite:

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

See [Self-Hosted Setup](self-hosted.md) for deploying the Lore server.
