# Lore — Universal AI Memory Layer

[![PyPI](https://img.shields.io/pypi/v/lore-sdk)](https://pypi.org/project/lore-sdk/)
[![npm](https://img.shields.io/npm/v/lore-sdk)](https://www.npmjs.com/package/lore-sdk)
[![Docker](https://img.shields.io/docker/v/amitpaz/lore?label=docker)](https://hub.docker.com/r/amitpaz/lore)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/github/license/amitpaz1/lore)](LICENSE)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)
[![Tests](https://img.shields.io/github/actions/workflow/status/amitpaz1/lore/ci.yml?label=tests)](https://github.com/amitpaz1/lore/actions)

**Your AI agents remember everything. Automatically.**

Lore is a cross-agent memory system that stores, connects, and retrieves knowledge across any AI agent — without code changes. Install a hook, and relevant memories appear in every prompt. No agent cooperation needed.

```
User: "What API rate limits should I use?"

── Lore hook fires (20ms) ──────────────────────────────
🧠 Relevant memories from Lore:
- [0.82] Stripe API returns 429 after 100 req/min — use exponential backoff
- [0.71] Our internal API rate limit is 500 req/min per API key
────────────────────────────────────────────────────────

Agent sees memories + prompt → responds with full context
```

## Features

### Universal Memory
`remember` · `recall` · `forget` · `list_memories` · `stats`

Store and retrieve memories across any AI agent via MCP tools, REST API, or Python/TypeScript SDK. Semantic search with tier-based TTL, importance scoring, temporal decay, and automatic PII redaction.

### Knowledge Graph
`graph_query` · `entity_map` · `related` · `extract_facts` · `list_facts` · `conflicts`

Entities and relationships auto-extracted from memories. Hop-by-hop graph traversal surfaces connected knowledge that pure vector search misses. Atomic fact extraction with automatic conflict detection.

### Graph Visualization
**Web UI at `/ui/`**

Interactive D3 force-directed graph of your knowledge base. Entity detail panels, topic clusters, search, and filtering. Runs in the browser — no install required.

### Session Continuity
**Auto-snapshot + auto-inject — zero agent cooperation**

The Session Accumulator automatically captures conversation context and injects relevant session history into every prompt. Deterministic (no LLM needed). Works via hooks — the agent never knows Lore exists.

### Recent Activity
`recent_activity`

Session-aware summary of what happened recently across all projects. Gives agents continuity between conversations without manual context-passing.

### Topic Notes
`topics` · `topic_detail`

Auto-generated concept hubs that cluster related memories, entities, and facts around recurring themes. See everything Lore knows about a topic in one view.

### Export & Snapshot
`export` · `snapshot` · `snapshot_list` · `save_snapshot`

Full data export in JSON and Markdown formats. Obsidian-compatible output for browsing your knowledge graph in a PKM tool. Snapshots for backup and migration.

### Approval UX
`review_digest` · `review_connection`

Review discovered knowledge graph connections before they become permanent. Approve, reject, or skip — keep your graph clean.

### Multi-Agent Setup
`lore setup claude-code` · `lore setup openclaw` · `lore setup cursor` · `lore setup codex`

One-command hook installation for all major AI coding agents. Auto-retrieval injected into every prompt — no code changes needed.

### Retrieval Analytics
`GET /v1/analytics/retrieval` · Prometheus metrics

Track hit rate, score distribution, memory utilization, and latency. Know whether memories are actually helping your agents.

## Quick Start

### Docker Compose (recommended)

```bash
git clone https://github.com/amitpaz1/lore.git
cd lore
docker compose up -d
```

Starts Postgres with pgvector and the Lore server on `http://localhost:8765`.

### pip

```bash
pip install lore-sdk[server]
lore serve  # starts on port 8765
```

### Verify it works

```bash
curl http://localhost:8765/v1/memories
```

## Multi-Agent Setup

### Claude Code

**Option A: Auto-retrieval hook (recommended)**

```bash
lore setup claude-code
```

This installs a `UserPromptSubmit` hook that auto-injects relevant memories into every prompt.

**Option B: MCP tools**

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "lore": {
      "command": "lore",
      "args": ["mcp"],
      "env": {
        "LORE_API_URL": "http://localhost:8765",
        "LORE_API_KEY": "your-api-key"
      }
    }
  }
}
```

### OpenClaw

```bash
lore setup openclaw
```

Installs a `message:preprocessed` hook for auto-retrieval. Memories appear in context before every agent response.

### Cursor

```bash
lore setup cursor
```

Installs a `beforeSubmitPrompt` hook. Also add MCP config to `.cursorrules`:

```json
{
  "mcpServers": {
    "lore": {
      "command": "lore",
      "args": ["mcp"],
      "env": {
        "LORE_API_URL": "http://localhost:8765",
        "LORE_API_KEY": "your-api-key"
      }
    }
  }
}
```

### Codex CLI

```bash
lore setup codex
```

Installs a `beforePlan` hook. Add MCP config:

```json
{
  "mcpServers": {
    "lore": {
      "command": "lore",
      "args": ["mcp"],
      "env": {
        "LORE_API_URL": "http://localhost:8765",
        "LORE_API_KEY": "your-api-key"
      }
    }
  }
}
```

### Any HTTP client

Auto-retrieval works with any system that can make an HTTP call before sending a prompt:

```bash
curl -s "http://localhost:8765/v1/retrieve?query=your+prompt&limit=5&min_score=0.3&format=markdown" \
  -H "Authorization: Bearer $LORE_API_KEY"
```

## MCP Tools Reference

| Tool | Description |
|------|-------------|
| `remember` | Store a memory with type, tier, tags, metadata |
| `recall` | Semantic search with temporal/graph-enhanced retrieval |
| `forget` | Delete a memory by ID |
| `list_memories` | List memories with filtering |
| `stats` | Memory statistics (total, by type/tier, importance) |
| `upvote_memory` | Boost memory ranking |
| `downvote_memory` | Lower memory ranking |
| `graph_query` | Hop-by-hop knowledge graph traversal |
| `entity_map` | List entities (optional D3 format) |
| `related` | Find related memories/entities |
| `extract_facts` | Extract (subject, predicate, object) triples |
| `list_facts` | List active facts |
| `conflicts` | List detected fact conflicts |
| `classify` | Intent, domain, emotion classification |
| `enrich` | LLM-powered metadata extraction |
| `consolidate` | Merge duplicate/related memories |
| `ingest` | Accept content from external sources |
| `github_sync` | Sync GitHub repo data |
| `check_freshness` | Verify memory freshness against git |
| `as_prompt` | Export memories formatted for LLM injection |
| `add_conversation` | Extract memories from conversation messages |
| `recent_activity` | Recent memory activity summary |
| `topics` | List auto-detected recurring topics |
| `topic_detail` | Deep dive on a topic (memories, entities, timeline) |
| `export` | Export all data to JSON |
| `snapshot` | Create data backup |
| `snapshot_list` | List available snapshots |
| `save_snapshot` | Save session snapshot |
| `review_digest` | Get pending connections for review |
| `review_connection` | Approve/reject a pending connection |
| `on_this_day` | Memories from same date across years |

## CLI Reference

```bash
# Memory operations
lore remember "API rate limit is 100 req/min" --tags api,limits
lore recall "rate limits" --limit 5
lore forget <memory-id>
lore memories --tier long_term
lore stats

# Knowledge graph
lore graph "authentication" --depth 2
lore entities --limit 50
lore facts "extract facts from this text"
lore conflicts

# Session & context
lore recent --hours 24
lore on-this-day

# Export & backup
lore export --format json > backup.json
lore import backup.json
lore snapshot-save --title "before refactor"

# Server & setup
lore serve                    # start HTTP server
lore mcp                     # start MCP server
lore ui                      # start web UI
lore setup claude-code       # install hooks
lore setup openclaw
lore setup cursor
lore setup codex

# API keys
lore keys-create --name "my-agent"
lore keys-list
lore keys-revoke <key-id>
```

## API Reference

### Key endpoints

```
GET    /v1/retrieve                    # Auto-retrieval (for hooks)
POST   /v1/memories                    # Create memory
POST   /v1/memories/search             # Semantic search
GET    /v1/memories                    # List memories
GET    /v1/memories/{id}               # Get memory
PATCH  /v1/memories/{id}               # Update memory
DELETE /v1/memories/{id}               # Delete memory

GET    /v1/graph                       # Knowledge graph
GET    /v1/graph/topics                # Topic list
GET    /v1/graph/topics/{name}         # Topic detail
GET    /v1/graph/entity/{id}           # Entity detail

POST   /v1/conversations              # Extract memories from conversation
POST   /v1/ingest                     # Ingest external content
POST   /v1/ingest/batch               # Batch ingest

GET    /v1/review                     # Pending connection reviews
POST   /v1/review/{id}               # Approve/reject connection

POST   /v1/export                     # Export all data
POST   /v1/import                     # Import data
POST   /v1/export/snapshots           # Create snapshot
GET    /v1/export/snapshots           # List snapshots

GET    /v1/recent                     # Recent activity
GET    /v1/analytics/retrieval        # Retrieval analytics + Prometheus

POST   /v1/keys                       # Create API key
GET    /v1/keys                       # List API keys
DELETE /v1/keys/{id}                  # Revoke API key
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | PostgreSQL connection string |
| `LORE_PORT` | `8765` | Server port |
| `LORE_API_KEY` | — | API key for authentication |
| `LORE_API_URL` | `http://localhost:8765` | Remote server URL |
| `LORE_PROJECT` | — | Default project scope |
| `LORE_SNAPSHOT_THRESHOLD` | `30000` | Characters before auto-snapshot |
| `LORE_ENRICHMENT_ENABLED` | `false` | Enable LLM enrichment pipeline |
| `LORE_ENRICHMENT_MODEL` | `gpt-4o-mini` | Model for enrichment |
| `LORE_LLM_PROVIDER` | — | LLM provider override |
| `LORE_LLM_API_KEY` | — | LLM API key |
| `LORE_LLM_MODEL` | — | LLM model override |
| `LORE_LLM_BASE_URL` | — | LLM base URL |
| `LORE_GRAPH_DEPTH` | `2` | Default graph traversal depth |
| `LORE_GRAPH_CONFIDENCE_THRESHOLD` | `0.5` | Entity confidence threshold |
| `LORE_HTTP_TIMEOUT` | `30` | HTTP timeout (seconds) |
| `OPENAI_API_KEY` | — | Auto-enables enrichment when set |

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      Agent Runtimes                          │
│  Claude Code · OpenClaw · Cursor · Codex · Any HTTP client   │
└──────────┬──────────────────────────────────┬────────────────┘
           │ hooks (auto-retrieval)           │ MCP tools
           ▼                                  ▼
┌──────────────────────────────────────────────────────────────┐
│                     Lore Server (:8765)                       │
│                                                              │
│  REST API · MCP Server · Web UI (/ui/)                       │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐ │
│  │  Embedder   │  │  Knowledge   │  │  LLM Pipeline       │ │
│  │  (ONNX)     │  │  Graph       │  │  (optional)         │ │
│  │             │  │  Engine      │  │  classify · enrich   │ │
│  │  pgvector   │  │  entities    │  │  extract · consolidate│
│  │  search     │  │  relations   │  │                     │ │
│  └─────────────┘  └──────────────┘  └─────────────────────┘ │
└──────────────────────────┬───────────────────────────────────┘
                           │
              ┌────────────▼────────────┐
              │   PostgreSQL + pgvector  │
              │   memories · entities    │
              │   relationships · facts  │
              └─────────────────────────┘
```

## Performance

| Operation | Latency |
|-----------|---------|
| `/v1/retrieve` (warm) | ~20ms |
| `remember()` (no LLM) | < 100ms |
| `recall()` 100 memories | < 50ms |
| `recall()` 10K memories | < 200ms |
| `recall()` graph-enhanced | < 500ms |
| Embedding (500 words) | < 200ms |

## Contributing

```bash
git clone https://github.com/amitpaz1/lore.git
cd lore
pip install -e ".[dev,server,mcp,enrichment]"
docker compose up -d db  # Postgres + pgvector
pytest
```

## License

MIT
