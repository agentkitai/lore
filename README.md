# Lore

[![Tests](https://img.shields.io/github/actions/workflow/status/amitpaz1/lore/ci.yml?label=tests)](https://github.com/amitpaz1/lore/actions)
[![License](https://img.shields.io/github/license/amitpaz1/lore)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-blue)](https://pypi.org/project/lore-sdk/)

**Give your AI a brain.** Universal memory layer for AI agents. MCP-native. Self-hosted. One `docker compose up` and your AI remembers everything.

---

## Quickstart (< 2 minutes)

```bash
# 1. Start Lore
git clone https://github.com/amitpaz1/lore.git && cd lore
docker compose up -d

# 2. Initialize your org + get an API key
curl -s -X POST http://localhost:8765/v1/org/init \
  -H "Content-Type: application/json" -d '{"name": "my-org"}' | python3 -m json.tool

# 3. Add this to your Claude Desktop config (see below)
```

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "lore": {
      "command": "python",
      "args": ["-m", "lore.mcp"],
      "env": {
        "LORE_PROJECT": "my-project"
      }
    }
  }
}
```

Restart Claude Desktop. Done. Claude can now remember and recall information across conversations.

---

## What Is Lore?

Lore gives AI agents persistent memory. Your AI learns something? It remembers it forever. Next conversation, next agent, next week — the knowledge is there.

**5 MCP tools** your AI gets:

| Tool | What it does | Example |
|------|-------------|---------|
| `remember` | Store a memory | "Remember that Stripe rate-limits at 100 req/min" |
| `recall` | Semantic search | "What do we know about rate limiting?" |
| `forget` | Delete memories | "Forget the outdated deployment notes" |
| `list` | Browse memories | "Show me all lessons tagged 'postgres'" |
| `stats` | Memory statistics | "How many memories do we have?" |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              AI Clients                              │
│  Claude Desktop · Cursor · Windsurf · Custom Agents  │
└──────────────────────┬──────────────────────────────┘
                       │ MCP (stdio)
┌──────────────────────▼──────────────────────────────┐
│              Lore MCP Server                         │
│  ┌────────┐ ┌──────┐ ┌──────┐ ┌────┐ ┌─────┐      │
│  │remember│ │recall│ │forget│ │list│ │stats│      │
│  └───┬────┘ └──┬───┘ └──┬───┘ └─┬──┘ └──┬──┘      │
│      └─────────┴────────┴───────┴───────┘           │
│                     │                                │
│         ┌───────────┼───────────┐                    │
│         ▼           ▼           ▼                    │
│   ┌──────────┐ ┌─────────┐ ┌────────┐              │
│   │ Embedder │ │ Storage │ │Redactor│              │
│   │(MiniLM)  │ │(SQLite/ │ │(opt-in)│              │
│   │ 384-dim  │ │ Postgres│ │        │              │
│   └──────────┘ └─────────┘ └────────┘              │
└──────────────────────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼                             ▼
 ┌──────────────┐            ┌──────────────────┐
 │ Local Mode   │            │ Server Mode      │
 │ SQLite       │            │ PostgreSQL +     │
 │ Zero config  │            │ pgvector         │
 │ Single user  │            │ Multi-tenant     │
 └──────────────┘            │ REST API         │
                             └──────────────────┘
```

**Two modes:**
- **Local mode** (default): SQLite + embedded ONNX model. Zero config. Perfect for single-user Claude Desktop.
- **Server mode**: PostgreSQL + pgvector. Multi-tenant, API keys, shared across teams. Use with `docker compose up`.

---

## MCP Setup

### Claude Desktop

```json
{
  "mcpServers": {
    "lore": {
      "command": "python",
      "args": ["-m", "lore.mcp"],
      "env": {
        "LORE_PROJECT": "my-project"
      }
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "lore": {
      "command": "python",
      "args": ["-m", "lore.mcp"],
      "env": {
        "LORE_PROJECT": "my-project"
      }
    }
  }
}
```

### Remote Mode (shared server)

Point the MCP client at your Lore server instead of using local SQLite:

```json
{
  "mcpServers": {
    "lore": {
      "command": "python",
      "args": ["-m", "lore.mcp"],
      "env": {
        "LORE_STORE": "remote",
        "LORE_API_URL": "http://localhost:8765",
        "LORE_API_KEY": "lore_sk_..."
      }
    }
  }
}
```

See [`examples/`](examples/) for ready-to-paste config files.

---

## Install

```bash
pip install lore-sdk
```

With MCP support:
```bash
pip install lore-sdk[mcp]
```

With server dependencies:
```bash
pip install lore-sdk[server]
```

---

## REST API Reference

All endpoints require `Authorization: Bearer lore_sk_...` header.

### Memories

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/memories` | Create a memory (server embeds automatically) |
| `GET` | `/v1/memories` | List memories (paginated, filterable) |
| `GET` | `/v1/memories/search?q=...` | Semantic search |
| `GET` | `/v1/memories/{id}` | Get a single memory |
| `DELETE` | `/v1/memories/{id}` | Delete a memory |
| `DELETE` | `/v1/memories?confirm=true` | Bulk delete with filters |
| `GET` | `/v1/stats` | Memory store statistics |

### Create a memory

```bash
curl -X POST http://localhost:8765/v1/memories \
  -H "Authorization: Bearer lore_sk_..." \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Stripe rate-limits at 100 req/min. Use exponential backoff.",
    "type": "lesson",
    "tags": ["stripe", "rate-limit"],
    "project": "payments"
  }'
```

### Search memories

```bash
curl "http://localhost:8765/v1/memories/search?q=rate+limiting&limit=5" \
  -H "Authorization: Bearer lore_sk_..."
```

### Organization setup

```bash
# First-run: create org and get API key
curl -X POST http://localhost:8765/v1/org/init \
  -H "Content-Type: application/json" \
  -d '{"name": "my-org"}'
# Returns: {"org_id": "...", "api_key": "lore_sk_...", "key_prefix": "lore_sk_..."}
```

---

## Self-Hosted Deployment

### Docker Compose (recommended)

```bash
git clone https://github.com/amitpaz1/lore.git && cd lore

# Development
docker compose up -d

# Production (with secure password)
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" > .env
docker compose -f docker-compose.prod.yml up -d
```

The stack includes:
- **Lore server** on port 8765
- **PostgreSQL 16 + pgvector** for storage and vector search
- Health checks, auto-restart, resource limits (production)

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | PostgreSQL connection string (server mode) |
| `LORE_STORE` | `local` | `local` (SQLite) or `remote` (HTTP to server) |
| `LORE_PROJECT` | — | Default project scope |
| `LORE_API_URL` | — | Server URL (remote mode) |
| `LORE_API_KEY` | — | API key (remote mode) |
| `LORE_DB_PATH` | `~/.lore/default.db` | SQLite path (local mode) |
| `LORE_MODEL_DIR` | `~/.lore/models` | Embedding model cache |
| `LORE_REDACT` | `false` | Enable PII redaction |

---

## Why Lore?

| | Lore | Mem0 | Zep | DIY pgvector |
|---|---|---|---|---|
| **MCP native** | Yes | No | No | No |
| **Self-hosted** | Yes | Paid cloud | Paid cloud | Yes |
| **Setup time** | 2 min | Account signup | Account signup | Hours |
| **Local mode** | Yes (SQLite) | No | No | No |
| **Embedding** | Built-in (ONNX) | API-dependent | Built-in | DIY |
| **Multi-tenant** | Yes | Yes | Yes | DIY |
| **Cost** | Free | $99+/mo | $99+/mo | Free + time |
| **Vendor lock-in** | None | High | High | None |

Lore is the only memory layer that's:
1. **MCP-native** — works directly with Claude Desktop, Cursor, Windsurf
2. **Zero-config local mode** — `pip install lore-sdk` and go, no server needed
3. **Self-hosted** — your data stays on your machine or your infra
4. **Open source** — MIT licensed, no usage limits, no telemetry

---

## How It Works

Lore uses **semantic search** powered by a local ONNX embedding model (all-MiniLM-L6-v2, 384 dimensions). No API calls, no data leaves your machine.

**Storing a memory:**
1. Content comes in via MCP tool or REST API
2. Text is embedded into a 384-dim vector (local ONNX, ~200ms)
3. Memory + embedding stored in SQLite (local) or PostgreSQL (server)
4. Optional PII redaction runs before embedding

**Recalling memories:**
1. Query text is embedded using the same model
2. Cosine similarity search against stored embeddings
3. Results ranked by: `similarity × time_decay` (newer memories score higher)
4. Filtered by type, tags, project as requested

---

## Python SDK

```python
from lore import Lore

client = Lore()  # local mode — zero config

# Store
client.remember(
    content="Stripe rate-limits at 100 req/min. Use exponential backoff.",
    type="lesson",
    tags=["stripe", "rate-limit"],
)

# Search
results = client.recall("stripe rate limiting", limit=5)

# List
memories = client.list(type="lesson", project="payments")

# Stats
stats = client.stats()
```

### Remote mode

```python
from lore import Lore

client = Lore(
    store="remote",
    api_url="http://localhost:8765",
    api_key="lore_sk_...",
)
```

---

## Features

- **Semantic search** — find memories by meaning, not keywords
- **Local-first** — SQLite + ONNX embeddings, no server needed
- **Multi-tenant server** — PostgreSQL + pgvector, API key auth
- **MCP native** — 5 tools for Claude Desktop, Cursor, Windsurf
- **Memory types** — note, lesson, snippet, fact, conversation, decision
- **Project scoping** — isolate memories by project
- **Tag filtering** — organize with tags, filter on recall
- **Time decay** — newer memories rank higher in search
- **PII redaction** — opt-in scrubbing of API keys, emails, IPs, etc.
- **REST API** — full CRUD + search, OpenAPI docs at `/docs`

---

## Contributing

Contributions welcome! Please open an issue first to discuss what you'd like to change.

```bash
# Development setup
git clone https://github.com/amitpaz1/lore.git && cd lore
pip install -e ".[dev,server,mcp]"
pytest
```

---

## License

MIT
