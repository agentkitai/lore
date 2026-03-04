# Architecture Document — Open Brain

**Author:** Solutions Architect (BMAD v6.0.4) | **Date:** 2026-03-03
**Status:** Draft
**Inputs:** [Product Brief](./product-brief.md), [PRD](./prd.md), Lore codebase analysis
**Project:** Open Brain (pivot from Lore)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Technology Stack](#2-technology-stack)
3. [Component Architecture](#3-component-architecture)
4. [Data Model](#4-data-model)
5. [MCP Tool Specifications](#5-mcp-tool-specifications)
6. [API Design](#6-api-design)
7. [Deployment Architecture](#7-deployment-architecture)
8. [Security Considerations](#8-security-considerations)
9. [Migration Plan](#9-migration-plan)
10. [Architecture Decision Records](#10-architecture-decision-records)
11. [Project Structure](#11-project-structure)
12. [Phased Implementation](#12-phased-implementation)

---

## 1. System Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         AI Clients                                   │
│  ┌──────────┐ ┌────────┐ ┌──────────┐ ┌─────────┐ ┌─────────────┐ │
│  │  Claude   │ │ Cursor │ │ Windsurf │ │ Copilot │ │Custom Agents│ │
│  │ Desktop   │ │        │ │          │ │         │ │             │ │
│  └─────┬─────┘ └───┬────┘ └────┬─────┘ └────┬────┘ └──────┬──────┘ │
└────────┼───────────┼──────────┼────────────┼──────────────┼────────┘
         │           │          │            │              │
         │     MCP Protocol (stdio)         │    MCP (SSE, P1)
         │           │          │            │              │
┌────────▼───────────▼──────────▼────────────▼──────────────▼────────┐
│                    Open Brain MCP Server                            │
│                                                                     │
│  ┌─────────┐ ┌────────┐ ┌────────┐ ┌──────┐ ┌───────┐            │
│  │remember │ │ recall │ │ forget │ │ list │ │ stats │            │
│  └────┬────┘ └───┬────┘ └───┬────┘ └──┬───┘ └───┬───┘            │
│       └──────────┴──────────┴─────────┴─────────┘                  │
│                          │                                          │
│               ┌──────────▼──────────┐                              │
│               │   Core Service      │                              │
│               │  (Business Logic)   │                              │
│               └──────────┬──────────┘                              │
│                          │                                          │
│          ┌───────────────┼───────────────┐                         │
│          ▼               ▼               ▼                         │
│  ┌──────────────┐ ┌────────────┐ ┌──────────────┐                 │
│  │  Embedding   │ │  Redaction │ │   Storage    │                 │
│  │  Pipeline    │ │  Pipeline  │ │   Layer      │                 │
│  │ (MiniLM-L6) │ │  (opt-in)  │ │  (asyncpg)   │                 │
│  └──────────────┘ └────────────┘ └──────┬───────┘                 │
└─────────────────────────────────────────┼─────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────────┐
│         REST API (FastAPI)               Webhook Ingestion      │
│  ┌────────────────────────┐       ┌───────────────────────┐     │
│  │ POST   /v1/memories    │       │ POST /v1/webhook      │     │
│  │ GET    /v1/memories    │       │ (field mapping,       │     │
│  │ GET    /v1/memories/   │       │  auto-embed)          │     │
│  │        search?q=...    │       └───────────┬───────────┘     │
│  │ DELETE /v1/memories/id │                   │                 │
│  │ GET    /v1/stats       │                   │                 │
│  └────────────┬───────────┘                   │                 │
│               └───────────────┬───────────────┘                 │
└───────────────────────────────┼─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   PostgreSQL + pgvector                          │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  ┌───────────┐ │
│  │ memories │  │   orgs   │  │   api_keys    │  │  users    │ │
│  │          │  │          │  │               │  │ (future)  │ │
│  └──────────┘  └──────────┘  └───────────────┘  └───────────┘ │
└─────────────────────────────────────────────────────────────────┘

External Clients:
┌─────────────────────┐  ┌──────────────────────┐
│   CLI Tool          │  │  Python/TS SDKs      │
│  (openbrain ...)    │  │  (pip/npm install)   │
│   ↓ REST API        │  │   ↓ REST API         │
└─────────────────────┘  └──────────────────────┘
```

### Component Inventory

| Component | Purpose | Exists in Lore? | Pivot Effort |
|-----------|---------|-----------------|--------------|
| MCP Server (stdio) | Primary interface for AI clients | ✅ Yes | Medium — rename tools, generalize schema |
| MCP Server (SSE) | Remote/networked MCP connections | ❌ No | New (P1) |
| REST API (FastAPI) | Programmatic access, webhook ingestion | ✅ Yes | Medium — new endpoints, schema change |
| Storage Layer (asyncpg) | Postgres + pgvector operations | ✅ Yes | Medium — new table, migration |
| Embedding Pipeline | Local ONNX MiniLM-L6-v2 | ✅ Yes | None |
| Redaction Pipeline | Optional PII scrubbing | ✅ Yes | Low — make opt-in (currently default) |
| CLI Tool | Terminal interface | ❌ No (Lore has basic CLI) | New |
| Python SDK | Programmatic Python interface | ✅ Yes | Medium — repackage + generalize |
| TypeScript SDK | Programmatic TS interface | ✅ Yes | Medium — repackage + generalize |
| Webhook Ingestion | Accept external data pushes | ❌ No | New (P1) |
| Auth System | API keys, RBAC, OIDC | ✅ Yes | Low — works as-is |

---

## 2. Technology Stack

| Layer | Technology | Version | Rationale |
|-------|-----------|---------|-----------|
| **Language** | Python | 3.11+ | Already used in Lore. Rich ML/embedding ecosystem. Solo dev — one language for server + SDK. |
| **Web Framework** | FastAPI | ≥0.100 | Already in Lore. Async, auto-OpenAPI docs, Pydantic validation. Best Python framework for APIs. |
| **MCP Framework** | FastMCP (mcp lib) | ≥1.0 | Already in Lore. Anthropic-maintained. Handles stdio transport, tool registration. |
| **Database** | PostgreSQL 16 | 16.x | Already in Lore. Mature, reliable, pgvector support. |
| **Vector Search** | pgvector | 0.7+ | Already in Lore. Eliminates need for separate vector DB. HNSW index for fast ANN. |
| **Embedding Model** | all-MiniLM-L6-v2 (ONNX) | — | Already in Lore. 384 dims, ~22MB model, runs locally. No API dependency. |
| **ONNX Runtime** | onnxruntime | ≥1.14 | Already in Lore. CPU inference, cross-platform. |
| **Tokenizer** | HuggingFace tokenizers | ≥0.13 | Already in Lore. Fast Rust-based tokenization. |
| **DB Driver** | asyncpg | ≥0.28 | Already in Lore. Native async Postgres driver, fastest Python option. |
| **HTTP Client** | httpx | ≥0.24 | Already in Lore. Async HTTP client for SDK remote mode. |
| **ID Generation** | python-ulid | ≥2.0 | Already in Lore. Sortable, unique, time-ordered IDs. |
| **Containerization** | Docker + Docker Compose | — | Already in Lore. Standard deployment, single `docker compose up`. |
| **CI/CD** | GitHub Actions | — | Already in Lore. Auto-test, auto-publish. |

### Stack Decisions

**Why NOT add new technologies:**
- No Redis — Postgres handles everything (rate limiting via in-memory, caching unnecessary at this scale)
- No separate vector DB (Pinecone, Qdrant) — pgvector in Postgres is sufficient for 100K+ memories and eliminates operational complexity
- No message queue — synchronous embedding on write is fine for V1 (< 500ms per memory)
- No TypeScript server — Python does both server and SDK; TS SDK is a thin HTTP client

---

## 3. Component Architecture

### 3.1 MCP Server

The MCP server is the **primary interface** for Open Brain. It exposes five tools over stdio transport (P0) and SSE transport (P1).

**Current state (Lore):** `src/lore/mcp/server.py` — 4 tools (save_lesson, recall_lessons, upvote_lesson, downvote_lesson), synchronous, uses local SQLite or remote HTTP.

**Target state (Open Brain):** `src/openbrain/mcp/server.py` — 5 tools (remember, recall, forget, list, stats), supports both local (embedded) and remote (API) backends.

#### Architecture

```
┌────────────────────────────────────────────────────┐
│              MCP Server Process                     │
│                                                     │
│  ┌──────────┐     ┌─────────────────────────┐      │
│  │ FastMCP  │────▶│  Tool Handlers          │      │
│  │ (stdio/  │     │  ┌─────────────────┐    │      │
│  │  SSE)    │     │  │ remember()      │    │      │
│  └──────────┘     │  │ recall()        │    │      │
│                   │  │ forget()        │    │      │
│                   │  │ list_memories() │    │      │
│  ┌──────────┐    │  │ stats()         │    │      │
│  │ Env Vars │    │  └────────┬────────┘    │      │
│  │ Config   │    └───────────┼─────────────┘      │
│  └──────────┘                │                     │
│                    ┌─────────▼─────────┐           │
│                    │  MemoryService    │           │
│                    │  (core logic)     │           │
│                    └─────────┬─────────┘           │
│                              │                     │
│              ┌───────────────┼───────────────┐     │
│              ▼               ▼               ▼     │
│     ┌──────────────┐ ┌────────────┐ ┌──────────┐ │
│     │ LocalStore   │ │ Embedder   │ │ Redactor │ │
│     │ (SQLite) or  │ │ (ONNX)    │ │ (opt-in) │ │
│     │ RemoteStore  │ └────────────┘ └──────────┘ │
│     │ (HTTP→API)   │                              │
│     └──────────────┘                              │
└────────────────────────────────────────────────────┘
```

**Key design decisions:**
- MCP server can run in **local mode** (embedded SQLite + local embeddings, zero config) or **remote mode** (HTTP calls to the FastAPI server)
- Local mode is for single-user setups (Claude Desktop). Remote mode is for shared/production use.
- Tool handlers are thin wrappers around `MemoryService` — the same service used by REST API

#### Entry Points

```python
# Local mode (Claude Desktop typical setup):
# python -m openbrain.mcp
# Entry point: openbrain-mcp

# Remote mode:
# OPENBRAIN_STORE=remote OPENBRAIN_API_URL=http://localhost:8765 OPENBRAIN_API_KEY=ob_sk_... python -m openbrain.mcp
```

#### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENBRAIN_STORE` | `local` | `local` (SQLite) or `remote` (HTTP to API server) |
| `OPENBRAIN_PROJECT` | *(none)* | Default project scope for all operations |
| `OPENBRAIN_API_URL` | — | API server URL (required for remote mode) |
| `OPENBRAIN_API_KEY` | — | API key (required for remote mode) |
| `OPENBRAIN_REDACT` | `false` | Enable PII redaction pipeline |
| `OPENBRAIN_DB_PATH` | `~/.openbrain/default.db` | SQLite path (local mode only) |
| `OPENBRAIN_MODEL_DIR` | `~/.openbrain/models` | Embedding model cache directory |

### 3.2 Storage Layer

#### Local Store (SQLite) — MCP local mode

Used when the MCP server runs locally (typical Claude Desktop setup). Zero-config, single-file database.

**Current (Lore):** `src/lore/store/sqlite.py` — `lessons` table, basic CRUD + embedding blob storage.

**Target:** `src/openbrain/store/sqlite.py` — `memories` table with the generalized schema. Client-side cosine similarity search (numpy).

```python
class SqliteStore(Store):
    """Local SQLite store for single-user MCP mode."""
    
    def save(self, memory: Memory) -> None: ...
    def get(self, memory_id: str) -> Optional[Memory]: ...
    def search(self, embedding: List[float], filters: SearchFilters, limit: int) -> List[SearchResult]: ...
    def list(self, filters: ListFilters, limit: int, offset: int) -> Tuple[List[Memory], int]: ...
    def delete(self, memory_id: str) -> bool: ...
    def delete_by_filter(self, filters: DeleteFilters) -> int: ...
    def stats(self) -> StoreStats: ...
```

#### Server Store (asyncpg + pgvector) — REST API / remote MCP

Used by the FastAPI server. Async, connection-pooled, leverages pgvector for ANN search.

**Current (Lore):** `src/lore/server/db.py` + `routes/lessons.py` — SQL queries inline in route handlers.

**Target:** Extract storage logic into a dedicated `ServerStore` class, keeping routes thin.

```python
class ServerStore:
    """Async Postgres store with pgvector search."""
    
    def __init__(self, pool: asyncpg.Pool): ...
    
    async def save(self, org_id: str, memory: MemoryCreate) -> str: ...
    async def get(self, org_id: str, memory_id: str) -> Optional[Memory]: ...
    async def search(self, org_id: str, embedding: List[float], filters: SearchFilters, limit: int) -> List[SearchResult]: ...
    async def list(self, org_id: str, filters: ListFilters, limit: int, offset: int) -> Tuple[List[Memory], int]: ...
    async def delete(self, org_id: str, memory_id: str) -> bool: ...
    async def delete_by_filter(self, org_id: str, filters: DeleteFilters) -> int: ...
    async def stats(self, org_id: str) -> StoreStats: ...
```

#### Remote Store (HTTP client) — SDK remote mode

Used by the Python SDK and MCP server in remote mode. Thin HTTP wrapper around the REST API.

**Current (Lore):** `src/lore/store/remote.py` — HTTP calls to Lore server.

**Target:** `src/openbrain/store/remote.py` — Updated endpoints and schema.

### 3.3 Embedding Pipeline

**No changes needed.** The existing ONNX MiniLM-L6-v2 pipeline works perfectly.

```
Text Input
    │
    ▼
┌──────────────────┐
│    Tokenizer     │  HuggingFace tokenizers (Rust)
│  (MiniLM vocab)  │  Max 256 tokens, padding
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  ONNX Runtime    │  CPU inference
│  (MiniLM-L6-v2)  │  ~22MB model
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Mean Pooling    │  Average token embeddings
│  + L2 Normalize  │  weighted by attention mask
└────────┬─────────┘
         │
         ▼
    384-dim float vector
```

**Performance:** ~200-400ms per embedding on CPU. Model loaded once, cached in memory.

**Graceful degradation:** If the model fails to load (missing file, OOM), memories are stored without embeddings. Search degrades to text-match only. Warning logged.

**Future (not V1):** Configurable embedding provider — allow `OPENBRAIN_EMBEDDING_PROVIDER=openai` to use text-embedding-3-small via API. This would remove the onnxruntime dependency for users who prefer smaller Docker images.

### 3.4 REST API (FastAPI)

**Current (Lore):** `src/lore/server/` — FastAPI app with lesson CRUD, search, export/import, sharing, rate limiting, auth, health checks, metrics.

**Target:** Rename endpoints, add webhook ingestion, keep all existing infrastructure (auth, rate limiting, middleware, health checks).

The FastAPI server serves dual purpose:
1. **REST API** for SDKs, CLI, and programmatic access
2. **Backend** for MCP server in remote mode

#### Server Startup Flow

```
uvicorn lore.server.app:app
    │
    ▼
lifespan() context manager
    │
    ├──▶ init_pool(DATABASE_URL)    # asyncpg connection pool
    │
    ├──▶ run_migrations()           # idempotent SQL migrations
    │
    ├──▶ load_embedding_model()     # warm up ONNX (NEW for server-side embedding)
    │
    └──▶ yield (server running)
         │
         └──▶ close_pool()
```

**Key change for Open Brain:** The server needs to generate embeddings server-side for REST API writes and webhook ingestion. Currently, Lore's server expects the client to send pre-computed embeddings. Open Brain server must embed on write.

### 3.5 Webhook Ingestion (P1)

New component. A configurable POST endpoint that accepts arbitrary JSON and stores it as a memory.

```
External Service                    Open Brain Server
┌──────────┐    POST /v1/webhook   ┌──────────────────┐
│ GitHub   │───────────────────────▶│ Field Mapper     │
│ Actions  │    {"text": "...",    │ (config-driven)  │
│          │     "channel": "ci"}  │                  │
└──────────┘                       │  text → content  │
                                   │  channel → source│
                                   └────────┬─────────┘
                                            │
                                   ┌────────▼─────────┐
                                   │ Auto-Embed       │
                                   │ + Store           │
                                   └──────────────────┘
```

**Field mapping configuration** (via env var or config file):

```yaml
# webhook_mappings.yaml (or OPENBRAIN_WEBHOOK_MAPPINGS env var as JSON)
default:
  content: "content"          # Which field maps to content
  type: "type"
  source: "source"
  tags: "tags"
  metadata: "metadata"
  project: "project"

# Named mappings for specific sources
github:
  content: "body"
  source: "'github'"          # Literal string (quoted)
  type: "'event'"
  metadata: "{ repo: repository.full_name, action: action }"
```

### 3.6 CLI Tool (P1)

Thin wrapper around the REST API. Ships with the Python package.

```
$ openbrain remember "deployment uses port 8080" --type note --tags infra,deploy
✅ Memory saved (ID: 01HQWX...)

$ openbrain recall "what port does deployment use"
Found 2 relevant memories:

──────────────────────────────────────────────
Memory 1  (score: 0.87)
Content: deployment uses port 8080
Type: note | Tags: infra, deploy
Created: 2026-03-03 10:30:00 UTC
──────────────────────────────────────────────

$ openbrain list --type lesson --limit 5
...

$ openbrain forget 01HQWX...
✅ Memory deleted

$ openbrain stats
Total memories: 142
By type: note (89), lesson (41), decision (12)
By project: webapp (67), infra (45), unscoped (30)
Oldest: 2026-02-15 | Newest: 2026-03-03
```

**Configuration** via env vars or `~/.openbrain.yaml`:

```yaml
api_url: http://localhost:8765
api_key: ob_sk_...
project: my-project
```

### 3.7 Python SDK

**Current (Lore):** `lore-sdk` on PyPI. Synchronous `Lore` class + async `LoreClient`.

**Target:** `openbrain` on PyPI. Both sync and async clients with generalized schema.

```python
from openbrain import OpenBrain

ob = OpenBrain()  # local mode — zero config

# Store a memory
memory_id = ob.remember(
    content="Stripe rate-limits at 100 req/min",
    type="lesson",
    tags=["stripe", "rate-limit"],
    metadata={"resolution": "Use exponential backoff starting at 1s"}
)

# Recall by semantic search
results = ob.recall("stripe rate limiting", limit=5)

# List memories
memories = ob.list(type="lesson", project="payments")

# Delete
ob.forget(memory_id)

# Stats
stats = ob.stats()
```

**Backward compatibility:** `from lore import Lore` will still work via a compatibility shim with deprecation warning, for at least 6 months.

### 3.8 TypeScript SDK (P1)

**Current (Lore):** `lore-sdk` on npm. Requires user-provided embedding function.

**Target:** `openbrain` on npm. HTTP client only (no local embedding — TS users use the server).

```typescript
import { OpenBrain } from 'openbrain';

const ob = new OpenBrain({
  apiUrl: 'http://localhost:8765',
  apiKey: 'ob_sk_...',
});

const id = await ob.remember({
  content: 'React 19 uses server components by default',
  type: 'note',
  tags: ['react', 'architecture'],
});

const results = await ob.recall('React architecture decisions');
```

---

## 4. Data Model

### 4.1 Full Schema (Target State)

```sql
-- ============================================================
-- Migration 006: Open Brain pivot — lessons → memories
-- ============================================================

-- Core memories table (replaces lessons)
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,                    -- ULID (sortable, unique)
    org_id      TEXT NOT NULL REFERENCES orgs(id),   -- Multi-tenant isolation
    content     TEXT NOT NULL,                        -- The actual memory content
    type        TEXT NOT NULL DEFAULT 'note',         -- note, lesson, snippet, fact, conversation, decision
    source      TEXT,                                 -- Origin: agent name, tool, webhook, cli, etc.
    project     TEXT,                                 -- Namespace scoping (free-text)
    tags        JSONB NOT NULL DEFAULT '[]',          -- Filterable tags array
    metadata    JSONB NOT NULL DEFAULT '{}',          -- Flexible key-value pairs
    embedding   vector(384),                          -- Semantic search vector (MiniLM-L6-v2)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ                           -- Optional TTL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_memories_org 
    ON memories(org_id);

CREATE INDEX IF NOT EXISTS idx_memories_org_project 
    ON memories(org_id, project);

CREATE INDEX IF NOT EXISTS idx_memories_org_type 
    ON memories(org_id, type);

CREATE INDEX IF NOT EXISTS idx_memories_created 
    ON memories(org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memories_tags 
    ON memories USING gin(tags);

-- HNSW index for vector search (works on empty tables unlike IVFFlat)
CREATE INDEX IF NOT EXISTS idx_memories_embedding 
    ON memories USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Existing tables (unchanged)
-- orgs: id, name, created_at
-- api_keys: id, org_id, name, key_hash, key_prefix, project, is_root, role, revoked_at, created_at, last_used_at
-- users: id, oidc_sub, email, display_name, role, org_id, created_at, last_seen_at, disabled_at
```

### 4.2 Table: `memories`

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | TEXT | NO | — | ULID (time-sortable unique ID) |
| `org_id` | TEXT (FK→orgs) | NO | — | Organization isolation |
| `content` | TEXT | NO | — | The memory content (free text) |
| `type` | TEXT | NO | `'note'` | Memory type: note, lesson, snippet, fact, conversation, decision |
| `source` | TEXT | YES | NULL | Where it came from (agent name, "cli", "webhook:github", etc.) |
| `project` | TEXT | YES | NULL | Project namespace for scoping |
| `tags` | JSONB | NO | `'[]'` | Array of string tags for filtering |
| `metadata` | JSONB | NO | `'{}'` | Arbitrary key-value pairs |
| `embedding` | vector(384) | YES | NULL | Semantic search embedding |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Last modification timestamp |
| `expires_at` | TIMESTAMPTZ | YES | NULL | Auto-deletion time (NULL = never) |

### 4.3 Core Data Types (Python)

```python
# src/openbrain/types.py

@dataclass
class Memory:
    """A single memory."""
    id: str
    content: str
    type: str = "note"
    source: Optional[str] = None
    project: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[bytes] = None  # Serialized float32 vector (local store)
    created_at: str = ""
    updated_at: str = ""
    expires_at: Optional[str] = None


@dataclass
class SearchResult:
    """A memory with its relevance score."""
    memory: Memory
    score: float


@dataclass
class StoreStats:
    """Summary statistics about the memory store."""
    total_count: int
    count_by_type: Dict[str, int]
    count_by_project: Dict[str, int]
    oldest_memory: Optional[str]  # ISO timestamp
    newest_memory: Optional[str]  # ISO timestamp
```

### 4.4 Pydantic Models (API)

```python
# src/openbrain/server/models.py

class MemoryCreateRequest(BaseModel):
    content: str = Field(..., min_length=1)
    type: str = Field(default="note")
    source: Optional[str] = None
    project: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    expires_at: Optional[datetime] = None
    # Embedding is NOT accepted from client — server generates it
    
class MemoryCreateResponse(BaseModel):
    id: str

class MemoryResponse(BaseModel):
    id: str
    content: str
    type: str
    source: Optional[str] = None
    project: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None

class MemorySearchResult(MemoryResponse):
    score: float

class MemorySearchResponse(BaseModel):
    memories: List[MemorySearchResult]

class MemoryListResponse(BaseModel):
    memories: List[MemoryResponse]
    total: int
    limit: int
    offset: int

class StatsResponse(BaseModel):
    total_count: int
    count_by_type: Dict[str, int]
    count_by_project: Dict[str, int]
    oldest_memory: Optional[datetime] = None
    newest_memory: Optional[datetime] = None

class WebhookRequest(BaseModel):
    """Flexible webhook payload. At minimum, requires `content`."""
    content: str = Field(..., min_length=1)
    type: Optional[str] = None
    source: Optional[str] = None
    project: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
```

---

## 5. MCP Tool Specifications

### 5.1 `remember` — Store a memory

```json
{
  "name": "remember",
  "description": "Store a memory for future recall. USE THIS WHEN: you learn something important, receive instructions to remember, want to save a decision/fact/lesson/code snippet for later. The content should be self-contained — include enough context that the memory is useful without the original conversation. DO NOT store trivial or temporary information.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "content": {
        "type": "string",
        "description": "The memory content. Be specific and self-contained."
      },
      "type": {
        "type": "string",
        "default": "note",
        "enum": ["note", "lesson", "snippet", "fact", "conversation", "decision"],
        "description": "Memory type. 'note' for general info, 'lesson' for problem/resolution pairs, 'snippet' for code, 'fact' for factual data, 'conversation' for summaries, 'decision' for architectural/design decisions."
      },
      "tags": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Tags for filtering. Use lowercase, hyphenated. e.g. ['postgres', 'rate-limit']"
      },
      "metadata": {
        "type": "object",
        "description": "Additional structured data. For lessons: {\"problem\": \"...\", \"resolution\": \"...\"}. For snippets: {\"language\": \"python\", \"file\": \"app.py\"}."
      },
      "project": {
        "type": "string",
        "description": "Project namespace. Overrides OPENBRAIN_PROJECT env var if set."
      },
      "source": {
        "type": "string",
        "description": "Where this memory came from (e.g. agent name, tool name)."
      }
    },
    "required": ["content"]
  }
}
```

**Response:** `"✅ Memory saved (ID: 01HQWX7K8M3N...)"` or `"❌ Failed to save: <error>"`

### 5.2 `recall` — Semantic search

```json
{
  "name": "recall",
  "description": "Search memories by semantic similarity. USE THIS WHEN: you need information that might have been stored previously, before starting a task to check for relevant context, or when the user asks 'do you remember...'. Be specific in your query — describe what you're looking for in natural language. GOOD queries: 'Stripe rate limiting strategy', 'React project architecture decisions'. BAD queries: 'help', 'stuff', 'everything'.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Natural language search query. Be specific."
      },
      "type": {
        "type": "string",
        "description": "Filter by memory type (e.g. 'lesson', 'decision')."
      },
      "tags": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Filter by tags (AND logic — all tags must match)."
      },
      "project": {
        "type": "string",
        "description": "Filter by project namespace."
      },
      "limit": {
        "type": "integer",
        "default": 5,
        "minimum": 1,
        "maximum": 20,
        "description": "Maximum results to return."
      }
    },
    "required": ["query"]
  }
}
```

**Response:**
```
Found 3 relevant memories:

────────────────────────────────────────────
Memory 1  (score: 0.87, id: 01HQWX7K8M3N...)
Type: lesson | Tags: stripe, rate-limit
Content: Stripe API returns 429 after 100 req/min. Fix: exponential backoff starting at 1s, cap at 32s.
Metadata: {"resolution": "exponential backoff", "confidence": 0.9}
Created: 2026-03-01T10:30:00Z
────────────────────────────────────────────
...
```

Or: `"No relevant memories found. Try a different query or broader terms."`

### 5.3 `forget` — Delete memories

```json
{
  "name": "forget",
  "description": "Delete one or more memories. USE THIS WHEN: a memory is outdated, incorrect, or no longer relevant. Pass an ID to delete a specific memory, or use filters to bulk-delete. Bulk delete without any filter requires confirm=true as a safety measure.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "id": {
        "type": "string",
        "description": "Specific memory ID to delete."
      },
      "tags": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Delete memories matching ALL these tags."
      },
      "type": {
        "type": "string",
        "description": "Delete memories of this type."
      },
      "project": {
        "type": "string",
        "description": "Delete memories in this project."
      },
      "confirm": {
        "type": "boolean",
        "default": false,
        "description": "Required for bulk delete without specific ID. Safety guard."
      }
    }
  }
}
```

**Response:**
- Single: `"✅ Memory 01HQWX... deleted"`
- Bulk: `"✅ Deleted 12 memories matching filters (type=note, project=old-project)"`
- Missing: `"❌ Memory 01HQWX... not found"`
- Safety: `"⚠️ Bulk delete requires 'confirm: true'. This would delete N memories."`

### 5.4 `list` — Browse memories

```json
{
  "name": "list",
  "description": "Browse and list memories with optional filters. Unlike 'recall', this does NOT use semantic search — it returns memories in chronological order. USE THIS WHEN: you want to see recent memories, browse by type/project/tags, or get an overview of what's stored.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "type": {
        "type": "string",
        "description": "Filter by memory type."
      },
      "tags": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Filter by tags (AND logic)."
      },
      "project": {
        "type": "string",
        "description": "Filter by project namespace."
      },
      "limit": {
        "type": "integer",
        "default": 20,
        "minimum": 1,
        "maximum": 100,
        "description": "Maximum results."
      },
      "offset": {
        "type": "integer",
        "default": 0,
        "description": "Pagination offset."
      }
    }
  }
}
```

**Response:**
```
Showing 20 of 142 memories (offset 0):

1. [01HQWX...] (note) deployment uses port 8080 — tags: infra | 2026-03-03
2. [01HQWV...] (lesson) Stripe rate-limits at 100 req/min — tags: stripe | 2026-03-01
...
```

### 5.5 `stats` — Memory statistics

```json
{
  "name": "stats",
  "description": "Get summary statistics about the memory store. Shows total count, breakdown by type and project, and date range.",
  "inputSchema": {
    "type": "object",
    "properties": {}
  }
}
```

**Response:**
```
Memory Store Statistics:
  Total memories: 142
  By type: note (89), lesson (41), decision (12)
  By project: webapp (67), infra (45), unscoped (30)
  Date range: 2026-02-15 to 2026-03-03
```

---

## 6. API Design

### 6.1 REST Endpoints

All endpoints require API key authentication via `Authorization: Bearer ob_sk_...` header.
All endpoints are scoped to the authenticated org (and optionally project, if the API key is project-scoped).

#### Memory CRUD

| Method | Path | Description | Request Body | Response |
|--------|------|-------------|-------------|----------|
| `POST` | `/v1/memories` | Create a memory | `MemoryCreateRequest` | `201` + `MemoryCreateResponse` |
| `GET` | `/v1/memories` | List memories (paginated) | Query params: `type`, `tags`, `project`, `limit`, `offset` | `200` + `MemoryListResponse` |
| `GET` | `/v1/memories/search` | Semantic search | Query params: `q` (required), `type`, `tags`, `project`, `limit` | `200` + `MemorySearchResponse` |
| `GET` | `/v1/memories/{id}` | Get single memory | — | `200` + `MemoryResponse` |
| `DELETE` | `/v1/memories/{id}` | Delete single memory | — | `204` |
| `DELETE` | `/v1/memories` | Bulk delete by filter | Query params: `type`, `tags`, `project`, `confirm=true` | `200` + `{"deleted": N}` |

#### Statistics

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| `GET` | `/v1/stats` | Memory store statistics | `200` + `StatsResponse` |

#### Webhook (P1)

| Method | Path | Description | Request Body | Response |
|--------|------|-------------|-------------|----------|
| `POST` | `/v1/webhook` | Ingest external data as memory | `WebhookRequest` or mapped JSON | `201` + `MemoryCreateResponse` |

#### Org Management (existing)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/org/init` | Create org + root API key (first-run only) |

#### Key Management (existing)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/keys` | Create API key |
| `GET` | `/v1/keys` | List API keys |
| `DELETE` | `/v1/keys/{id}` | Revoke API key |

#### Health & Monitoring

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Simple health check |
| `GET` | `/ready` | Readiness probe (DB + pgvector) |
| `GET` | `/metrics` | Prometheus metrics (P2) |

### 6.2 API Key Format Change

**From:** `lore_sk_<hex>`
**To:** `ob_sk_<hex>`

The auth system will accept **both** formats during the transition period. New keys use the `ob_sk_` prefix.

### 6.3 Search Endpoint Detail

The `GET /v1/memories/search` endpoint is the REST equivalent of the `recall` MCP tool. Unlike Lore, which required the client to send pre-computed embeddings via POST, Open Brain computes embeddings server-side.

```
GET /v1/memories/search?q=stripe+rate+limiting&type=lesson&limit=5
Authorization: Bearer ob_sk_...

Response 200:
{
  "memories": [
    {
      "id": "01HQWX7K8M3N...",
      "content": "Stripe API returns 429 after 100 req/min...",
      "type": "lesson",
      "tags": ["stripe", "rate-limit"],
      "metadata": {"resolution": "exponential backoff"},
      "score": 0.87,
      "created_at": "2026-03-01T10:30:00Z",
      "updated_at": "2026-03-01T10:30:00Z"
    }
  ]
}
```

**Key change:** Search is now GET with a `q` parameter instead of POST with an embedding body. The server embeds the query internally. This is a simpler API contract — clients don't need embedding capabilities.

### 6.4 Webhook Endpoint Detail (P1)

```
POST /v1/webhook
Authorization: Bearer ob_sk_...
Content-Type: application/json

{
  "content": "Deployed v2.3.1 to production",
  "type": "event",
  "source": "github-actions",
  "tags": ["deploy", "production"],
  "metadata": {
    "commit": "abc123",
    "environment": "prod",
    "status": "success"
  }
}

Response 201:
{
  "id": "01HQWY..."
}
```

With field mapping (configured server-side), the webhook can accept arbitrary payloads:

```
POST /v1/webhook?mapping=github
Authorization: Bearer ob_sk_...
Content-Type: application/json

{
  "action": "completed",
  "workflow_run": {
    "name": "CI",
    "conclusion": "success"
  },
  "repository": {
    "full_name": "amitpaz1/openbrain"
  }
}
```

The server maps fields according to the configured `github` mapping template.

---

## 7. Deployment Architecture

### 7.1 Docker Compose (Primary Deployment)

```yaml
# docker-compose.yml — Open Brain Development
services:
  openbrain:
    build:
      context: .
      dockerfile: Dockerfile.server
    ports:
      - "${OPENBRAIN_PORT:-8765}:8765"
    environment:
      DATABASE_URL: postgresql://openbrain:${POSTGRES_PASSWORD:-openbrain}@db:5432/openbrain
      OPENBRAIN_REDACT: ${OPENBRAIN_REDACT:-false}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - model_cache:/home/openbrain/.openbrain/models
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M

  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: openbrain
      POSTGRES_USER: openbrain
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-openbrain}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U openbrain"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped

volumes:
  pgdata:
  model_cache:
```

### 7.2 Dockerfile

```dockerfile
# Dockerfile.server — Open Brain
# ── Stage 1: Build ──
FROM python:3.11-slim AS builder
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install ".[server]"

# ── Stage 2: Runtime ──
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r openbrain && useradd -r -g openbrain -d /app -m openbrain

COPY --from=builder /install /usr/local
COPY migrations/ migrations/

# Embedding model will be downloaded on first request and cached to volume
RUN mkdir -p /home/openbrain/.openbrain/models && chown -R openbrain:openbrain /home/openbrain

USER openbrain
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')" || exit 1

CMD ["uvicorn", "openbrain.server.app:app", "--host", "0.0.0.0", "--port", "8765"]
```

### 7.3 First-Run Experience

```bash
# 1. Clone
git clone https://github.com/amitpaz1/openbrain.git
cd openbrain

# 2. Start
docker compose up -d

# 3. Wait for healthy (DB + migrations + model download)
docker compose logs -f openbrain  # Watch for "Application startup complete"

# 4. Initialize org + get API key
curl -s -X POST http://localhost:8765/v1/org/init \
  -H "Content-Type: application/json" \
  -d '{"name": "my-org"}' | jq .

# Returns: {"org_id": "...", "api_key": "ob_sk_...", "key_prefix": "ob_sk_..."}

# 5. Copy MCP config to Claude Desktop
# See README for copy-paste JSON config
```

### 7.4 Claude Desktop MCP Config

```json
{
  "mcpServers": {
    "openbrain": {
      "command": "python",
      "args": ["-m", "openbrain.mcp"],
      "env": {
        "OPENBRAIN_STORE": "remote",
        "OPENBRAIN_API_URL": "http://localhost:8765",
        "OPENBRAIN_API_KEY": "ob_sk_your_key_here",
        "OPENBRAIN_PROJECT": "my-project"
      }
    }
  }
}
```

**Alternative (local mode, no server needed):**

```json
{
  "mcpServers": {
    "openbrain": {
      "command": "python",
      "args": ["-m", "openbrain.mcp"],
      "env": {
        "OPENBRAIN_PROJECT": "my-project"
      }
    }
  }
}
```

### 7.5 Resource Requirements

| Component | RAM | CPU | Disk | Notes |
|-----------|-----|-----|------|-------|
| Open Brain server | ~200-300MB | 0.5 core | 100MB (image) | Model loads on first request (~22MB) |
| PostgreSQL + pgvector | ~100-200MB | 0.5 core | Variable | Grows with data |
| **Total** | **~400-500MB** | **1 core** | **200MB base** | Fits a $5/mo VPS |

---

## 8. Security Considerations

### 8.1 Authentication

| Interface | Auth Method | Details |
|-----------|------------|---------|
| MCP stdio | None | Local process — inherits OS user permissions. Secure by design. |
| MCP SSE (P1) | API key header | `Authorization: Bearer ob_sk_...` |
| REST API | API key header | `Authorization: Bearer ob_sk_...` (existing Lore implementation) |
| Webhook | API key header | Same as REST API |

### 8.2 API Key Security

- **Format:** `ob_sk_<32 hex chars>` (128 bits of entropy)
- **Storage:** SHA-256 hash stored in DB. Raw key shown only once at creation.
- **Lookup:** Hash-based lookup + timing-safe comparison (existing in Lore)
- **Caching:** In-memory cache with 60s TTL to reduce DB lookups (existing)
- **Scoping:** Keys can be scoped to a specific project (existing)
- **RBAC:** reader/writer/admin roles (existing)

### 8.3 Data Isolation

- All queries include `org_id` in WHERE clause — no cross-org data leaks
- Project-scoped API keys enforce project filtering at the auth layer
- No ambient authority — every request must present a valid API key

### 8.4 Optional Redaction (FR-023)

**Default: DISABLED** (change from Lore's default of enabled).

When `OPENBRAIN_REDACT=true`:
- API keys, passwords, emails, IPs, credit card numbers are scrubbed before storage
- Redaction happens before embedding generation
- Original content is NOT retained
- Uses existing Lore redaction pipeline (regex-based, Luhn validation for CCs)

**Rationale for disabling by default:** Open Brain is general-purpose memory. Users storing conversation summaries, decisions, or code snippets don't want aggressive redaction mangling their content. Redaction is opt-in for users who know they're storing sensitive operational data.

### 8.5 Network Security

- Default Docker Compose binds to `localhost` only for DB (no external Postgres exposure)
- Server binds to `0.0.0.0:8765` — user must firewall or reverse-proxy for external access
- No TLS in application (expected behind reverse proxy — nginx, Caddy, Cloudflare Tunnel)
- No telemetry, no phone-home, no external API calls (except embedding model download on first run)

### 8.6 Input Validation

- Pydantic models validate all API inputs
- SQL injection prevented via parameterized queries (asyncpg)
- Content length limit: 100KB per memory (prevents abuse)
- Rate limiting: configurable, default 60 req/min per API key (existing)
- Embedding dimension validation: exactly 384 floats

---

## 9. Migration Plan

### 9.1 Database Migration: `lessons` → `memories`

This is the most critical migration. It must be:
1. **Idempotent** — safe to run multiple times
2. **Non-destructive** — doesn't drop the `lessons` table until validated
3. **Data-preserving** — all existing lesson data is carried over

#### Migration SQL (file: `migrations/006_openbrain_pivot.sql`)

```sql
-- Migration 006: Open Brain pivot — lessons → memories
-- Idempotent — safe to run multiple times
-- NON-DESTRUCTIVE: lessons table is preserved; memories table is created alongside

-- 1. Create the memories table (if not exists)
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    content     TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'note',
    source      TEXT,
    project     TEXT,
    tags        JSONB NOT NULL DEFAULT '[]',
    metadata    JSONB NOT NULL DEFAULT '{}',
    embedding   vector(384),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ
);

-- 2. Create indexes
CREATE INDEX IF NOT EXISTS idx_memories_org ON memories(org_id);
CREATE INDEX IF NOT EXISTS idx_memories_org_project ON memories(org_id, project);
CREATE INDEX IF NOT EXISTS idx_memories_org_type ON memories(org_id, type);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_tags ON memories USING gin(tags);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_embedding') THEN
        CREATE INDEX idx_memories_embedding ON memories 
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
    END IF;
END $$;

-- 3. Migrate data from lessons → memories (skip if already done)
-- Uses INSERT ... ON CONFLICT to be idempotent
INSERT INTO memories (id, org_id, content, type, source, project, tags, metadata, embedding, created_at, updated_at, expires_at)
SELECT 
    id,
    org_id,
    -- Combine problem + resolution into content
    CASE 
        WHEN resolution IS NOT NULL AND resolution != '' 
        THEN problem || E'\n\n' || resolution
        ELSE problem
    END AS content,
    'lesson' AS type,
    source,
    project,
    COALESCE(tags, '[]'::jsonb) AS tags,
    -- Merge meta with context, confidence, upvotes, downvotes
    jsonb_strip_nulls(
        COALESCE(meta, '{}'::jsonb) || jsonb_build_object(
            'context', context,
            'confidence', confidence,
            'upvotes', upvotes,
            'downvotes', downvotes,
            'migrated_from', 'lore_lessons'
        )
    ) AS metadata,
    embedding,
    created_at,
    updated_at,
    expires_at
FROM lessons
ON CONFLICT (id) DO NOTHING;

-- 4. DO NOT DROP lessons table — keep it for rollback safety
-- The lessons table can be dropped in a future migration (007+) after validation
```

### 9.2 Code Rename Plan

The rename is the second-largest effort. The approach is to rename in-place (see ADR-004).

#### Package Rename

| What | From | To |
|------|------|-----|
| Python package (import) | `lore` | `openbrain` |
| Python package (PyPI) | `lore-sdk` | `openbrain` |
| npm package | `lore-sdk` | `openbrain` |
| Docker image | — | `ghcr.io/amitpaz1/openbrain` |
| MCP server name | `lore` | `openbrain` |
| API key prefix | `lore_sk_` | `ob_sk_` |
| Environment vars | `LORE_*` | `OPENBRAIN_*` |
| Config paths | `~/.lore/` | `~/.openbrain/` |

#### File Rename

```bash
# Source code
mv src/lore src/openbrain

# Update all imports
find src/ -name "*.py" -exec sed -i 's/from lore\./from openbrain./g' {} +
find src/ -name "*.py" -exec sed -i 's/import lore/import openbrain/g' {} +

# Update pyproject.toml: name, packages, entry points
# Update Dockerfile: module references
# Update docker-compose: service names, env vars
```

#### Backward Compatibility Shim

```python
# src/lore/__init__.py (keep as compatibility layer)
"""
Lore has been renamed to Open Brain.
This compatibility shim will be removed in a future version.
"""
import warnings
warnings.warn(
    "The 'lore' package has been renamed to 'openbrain'. "
    "Please update your imports: from openbrain import OpenBrain",
    DeprecationWarning,
    stacklevel=2,
)
from openbrain import *  # noqa: F401, F403
```

### 9.3 Migration Sequence

1. **Create `memories` table + indexes** (migration 006)
2. **Copy data from `lessons` → `memories`** (in same migration)
3. **Deploy new code** (reads from `memories` table)
4. **Validate** (check data integrity, run tests)
5. **[Future, optional]** Drop `lessons` table (migration 007, after confidence)

### 9.4 Rollback Plan

If the migration fails:
1. New code checks for `memories` table — if absent, falls back to `lessons` 
2. `lessons` table is never modified or dropped
3. Rolling back code to Lore version restores full functionality
4. The `ON CONFLICT DO NOTHING` clause means re-running migration is safe

---

## 10. Architecture Decision Records

### ADR-001: MCP-First vs REST-First

**Status:** Accepted

**Context:** Open Brain needs both MCP (for AI clients) and REST (for programmatic access) interfaces. The question is which is the "primary" interface that drives design decisions.

**Options:**
1. **MCP-first:** Design tools first, REST mirrors them
2. **REST-first:** Design REST API first, MCP wraps it
3. **Equal peers:** Design both simultaneously

**Decision:** **MCP-first** (Option 1)

**Rationale:**
- MCP is the primary distribution channel — every MCP-compatible AI becomes a distribution point
- The five MCP tools (`remember`, `recall`, `forget`, `list`, `stats`) ARE the product concept
- REST API exists to serve the MCP server in remote mode, SDKs, CLI, and webhooks — it's infrastructure, not the headline
- MCP tool naming drives REST endpoint naming (not the reverse)
- The product brief explicitly states: "Not a REST API with an MCP wrapper bolted on"

**Consequences:**
- REST endpoints mirror MCP tools 1:1 (plus webhook, health, org/key management)
- MCP tool descriptions get the most attention (they're the user-facing docs for AI)
- MCP can run standalone (local mode with SQLite) without the REST server

### ADR-002: Flexible Schema vs Typed Collections

**Status:** Accepted

**Context:** Lore uses a fixed schema (`problem` + `resolution`). Open Brain needs to support many memory types. Two approaches: one flexible table, or separate tables/collections per type.

**Options:**
1. **Single `memories` table** with `content` (text) + `type` (enum-ish) + `metadata` (JSONB)
2. **Multiple tables** per type (`notes`, `lessons`, `snippets`, `decisions`)
3. **Collection-based** — table-per-collection with dynamic schema

**Decision:** **Single table with type + metadata** (Option 1)

**Rationale:**
- Solo dev — one table is simpler to maintain, migrate, query
- `metadata` JSONB handles type-specific fields (lessons have `problem`/`resolution` in metadata, snippets have `language`)
- All types share the same search/list/delete operations — no reason to separate
- pgvector HNSW index works on one table; multiple tables = multiple indexes = more complexity
- The `type` field is informational, not structural — new types can be added without schema changes

**Consequences:**
- No schema enforcement for type-specific fields (metadata is free-form)
- Queries can filter by type efficiently (indexed)
- Migration from Lore's `lessons` is a simple data copy
- If type-specific validation is needed later, it's done in application code, not DB constraints

**Trade-offs:**
- Less strict than typed collections — metadata won't catch missing fields
- All memories share one HNSW index — no per-type index optimization
- These are acceptable for a solo dev project at this scale

### ADR-003: Embedding Model Choice (Local vs API)

**Status:** Accepted

**Context:** Open Brain needs embeddings for semantic search. Choose between local model (runs in process) vs external API (OpenAI, Cohere).

**Options:**
1. **Local only** — ONNX MiniLM-L6-v2 (384 dims), runs on CPU, ~22MB model
2. **API only** — OpenAI text-embedding-3-small (1536 dims), requires API key + internet
3. **Local default, API optional** — local by default, configurable to use API

**Decision:** **Local default, API optional in future** (Option 3, but V1 is local-only)

**Rationale:**
- Zero external dependencies is a key differentiator ("no OpenAI API key needed")
- MiniLM-L6-v2 is well-proven, 384 dims is efficient for pgvector
- Local embedding means the Docker container works fully offline after initial model download
- ~200-400ms embedding time is acceptable for synchronous writes in V1
- API option can be added later (P2+) for users who want higher quality embeddings

**Consequences:**
- Docker image is ~300-400MB (ONNX runtime + model)
- First request has cold-start delay (~2-5s to download model if not cached)
- Model volume mount in Docker Compose for persistence
- 384 dimensions is baked into the schema — changing later requires re-embedding all data

**Risk:** If we ever change the embedding model (different dimensions), all existing embeddings become incompatible. Mitigation: re-embedding migration script, or keep 384-dim as the canonical format.

### ADR-004: Mono-Repo Pivot vs New Repo

**Status:** Accepted

**Context:** Should Open Brain be built by renaming/pivoting the existing `amitpaz1/lore` repo, or by creating a fresh `amitpaz1/openbrain` repo?

**Options:**
1. **Pivot in-place** — rename repo, rename code, migrate
2. **Fresh repo** — start new, copy relevant code
3. **Fork** — fork lore, rename in fork

**Decision:** **Pivot in-place** (Option 1)

**Rationale:**
- Preserves full Git history (every commit, every decision)
- Preserves CI/CD pipeline, GitHub Actions, existing issues
- Avoids the work of setting up a new repo from scratch
- Lore has 2 GitHub stars — no significant community to disrupt
- GitHub supports repo renames with automatic redirects
- The codebase IS the product — copying selected files risks missing dependencies

**Consequences:**
- Repo URL changes: `amitpaz1/lore` → `amitpaz1/openbrain` (GitHub redirects old URLs)
- Git log contains "lore" references in old commits — that's fine
- `lessons` table stays in migrations for backward compatibility
- Need to update: pyproject.toml, package.json, Dockerfile, docker-compose, all imports
- PyPI: publish new `openbrain` package, deprecate `lore-sdk`
- npm: publish new `openbrain` package, deprecate `lore-sdk`

### ADR-005: SSE Transport Timing (P0 vs P1)

**Status:** Accepted

**Context:** MCP supports two transports: stdio (local) and SSE (networked). Should SSE be in the initial release?

**Options:**
1. **P0** — ship SSE in V1
2. **P1** — ship stdio in V1, add SSE in month 2-3

**Decision:** **P1** (Option 2)

**Rationale:**
- stdio is sufficient for the primary persona (Claude Desktop user running locally)
- SSE adds complexity: auth, CORS, connection management, reconnection handling
- The FastMCP library supports SSE, but it's less battle-tested than stdio
- Remote MCP clients can use the REST API + SDK as a workaround until SSE ships
- Shipping faster (stdio only) beats shipping complete (stdio + SSE)

**Consequences:**
- V1 MCP server only works locally (same machine as the AI client)
- Cloud/remote MCP connections require SSE (P1 deliverable)
- REST API provides remote access for SDKs and CLI from day 1
- SSE implementation is straightforward when needed (FastMCP supports it)

### ADR-006: Server-Side Embedding for REST API

**Status:** Accepted

**Context:** Lore's REST API requires clients to send pre-computed embeddings in search requests (POST with embedding vector). This forces every client to have an embedding model. Should Open Brain compute embeddings server-side?

**Options:**
1. **Client-side embedding** (Lore's current approach) — client sends vectors
2. **Server-side embedding** — server embeds on write and search
3. **Both** — accept raw text OR pre-computed embeddings

**Decision:** **Server-side embedding** (Option 2)

**Rationale:**
- Dramatically simplifies client SDKs — no embedding model dependency
- Search becomes `GET /v1/memories/search?q=text` instead of `POST` with vector
- Webhook ingestion needs server-side embedding (external services can't embed)
- The server already runs ONNX — adding embedding to write/search handlers is minimal
- One embedding model instance (server) instead of N (every client)

**Consequences:**
- Server does more work — embedding on every write and search query
- Server needs more RAM (~200MB for ONNX model)
- REST API is simpler (text in, results out — no vectors in the API contract)
- Existing Lore client SDK (which sends embeddings) needs updating
- If a client wants to use a different embedding model, they can't (server decides)

### ADR-007: API Key Prefix Change

**Status:** Accepted

**Context:** Lore uses `lore_sk_` API key prefix. Should Open Brain change it?

**Options:**
1. **Keep `lore_sk_`** — backward compatible, no migration
2. **Change to `ob_sk_`** — clean break, brand alignment
3. **Support both** — accept either prefix, new keys use `ob_sk_`

**Decision:** **Support both, new keys use `ob_sk_`** (Option 3)

**Rationale:**
- Existing Lore users don't need to regenerate keys
- New branding is clean from day one
- Auth system just checks the hash — prefix is cosmetic
- Migration is graceful: old keys work, new keys have new prefix

**Consequences:**
- Auth code accepts both `lore_sk_` and `ob_sk_` prefixes
- Org init generates `ob_sk_` keys
- Documentation shows `ob_sk_` only
- `lore_sk_` support can be deprecated in a future version

### ADR-008: Dropping Scoring Complexity

**Status:** Accepted

**Context:** Lore's search scoring uses `cosine_similarity × confidence × time_decay × vote_factor`. Open Brain's flexible schema drops `confidence`, `upvotes`, and `downvotes` as first-class columns. Should the scoring formula change?

**Options:**
1. **Keep full scoring** — derive confidence/votes from metadata
2. **Simplify to cosine similarity only** — rank by vector similarity
3. **Cosine + recency** — similarity with time decay, no votes/confidence

**Decision:** **Cosine + recency** (Option 3)

**Rationale:**
- General-purpose memories don't have meaningful "confidence" values
- Vote-based ranking was specific to the "lessons learned" use case
- Time decay is universally useful — recent memories should rank higher
- Simpler scoring = easier to understand and debug
- If needed, scoring can be enriched later based on actual usage patterns

**Scoring formula:**
```
score = cosine_similarity(query_embedding, memory_embedding) × time_decay
time_decay = exp(-λ × age_days)    where λ ≈ 0.005 (half-life ≈ 139 days)
```

**Consequences:**
- No more upvote/downvote MCP tools (removed)
- Scoring is transparent and predictable
- Migrated lessons have votes preserved in metadata (for reference, not scoring)
- Time decay half-life is generous (139 days) — memories stay relevant longer than Lore's 30 days

---

## 11. Project Structure

### Target File/Folder Layout After Pivot

```
openbrain/                          # Repo root (renamed from lore/)
├── .github/
│   └── workflows/
│       ├── ci.yml                  # Tests on push/PR
│       └── release.yml             # Build + publish Docker image + PyPI + npm
├── docker-compose.yml              # Development (with hot-reload)
├── docker-compose.prod.yml         # Production
├── Dockerfile.server               # Multi-stage Python server image
├── pyproject.toml                  # Python project config (openbrain)
├── README.md                       # New README with quickstart
├── LICENSE                         # MIT
├── .env.example                    # Environment variable template
│
├── src/
│   └── openbrain/
│       ├── __init__.py             # Package init, exports OpenBrain class
│       ├── __main__.py             # python -m openbrain
│       ├── types.py                # Memory, SearchResult, StoreStats dataclasses
│       ├── exceptions.py           # MemoryNotFoundError, etc.
│       ├── openbrain.py            # Main OpenBrain SDK class (renamed from lore.py)
│       ├── prompt.py               # Format memories for prompt injection
│       ├── client.py               # Async OpenBrainClient (renamed from LoreClient)
│       ├── cli.py                  # CLI entry point (openbrain remember/recall/...)
│       │
│       ├── embed/
│       │   ├── __init__.py
│       │   ├── base.py             # Embedder abstract class
│       │   └── local.py            # ONNX MiniLM-L6-v2 embedder
│       │
│       ├── redact/
│       │   ├── __init__.py
│       │   ├── patterns.py         # Regex patterns
│       │   └── pipeline.py         # RedactionPipeline
│       │
│       ├── store/
│       │   ├── __init__.py
│       │   ├── base.py             # Store abstract class
│       │   ├── sqlite.py           # Local SQLite store
│       │   ├── memory.py           # In-memory store (testing)
│       │   └── remote.py           # HTTP remote store
│       │
│       ├── mcp/
│       │   ├── __init__.py
│       │   └── server.py           # MCP server (5 tools: remember, recall, forget, list, stats)
│       │
│       └── server/
│           ├── __init__.py
│           ├── app.py              # FastAPI application
│           ├── config.py           # Settings from env vars
│           ├── db.py               # asyncpg pool + migration runner
│           ├── auth.py             # API key + OIDC auth
│           ├── middleware.py       # CORS, logging, rate limiting
│           ├── models.py           # Pydantic request/response models
│           ├── store.py            # ServerStore (async Postgres operations) — NEW
│           ├── embed.py            # Server-side embedding singleton — NEW
│           ├── secrets.py          # Docker secrets / env resolution
│           ├── logging_config.py   # Structured logging setup
│           ├── metrics.py          # Prometheus metrics
│           └── routes/
│               ├── __init__.py
│               ├── memories.py     # Memory CRUD + search endpoints (renamed from lessons.py)
│               ├── webhook.py      # Webhook ingestion endpoint — NEW (P1)
│               └── keys.py         # API key management
│
├── migrations/
│   ├── 001_initial.sql             # Original Lore schema (orgs, api_keys, lessons)
│   ├── 004_sharing.sql             # Sharing tables (may be deprecated)
│   ├── 005_oidc_and_rbac.sql       # OIDC + RBAC
│   └── 006_openbrain_pivot.sql     # NEW: memories table + data migration
│
├── ts/                             # TypeScript SDK
│   ├── package.json
│   ├── tsconfig.json
│   ├── src/
│   │   ├── index.ts
│   │   └── client.ts
│   └── README.md
│
├── tests/
│   ├── test_openbrain.py           # SDK unit tests
│   ├── test_embedding.py           # Embedding pipeline tests
│   ├── test_redaction.py           # Redaction tests
│   ├── test_mcp_server.py          # MCP tool tests
│   ├── test_api.py                 # REST API integration tests
│   └── test_migration.py           # Migration tests — NEW
│
├── examples/
│   ├── basic_usage.py
│   ├── claude_desktop_config.json  # Copy-paste MCP config
│   ├── cursor_config.json
│   └── windsurf_config.json
│
├── docs/
│   ├── self-hosted.md
│   ├── api-reference.md
│   ├── mcp-setup.md
│   └── migration-from-lore.md      # NEW
│
└── _bmad-output/                   # BMAD artifacts (not published)
    └── planning-artifacts/
        ├── product-brief.md
        ├── prd.md
        └── architecture.md         # This document
```

### Files Removed/Deprecated

| File | Action | Reason |
|------|--------|--------|
| `src/lore/server/routes/sharing.py` | Deprecate | Sharing features not part of Open Brain V1 |
| `src/lore/server/routes/sharing.py` routes | Remove from app.py | Not needed for V1, clutters API |
| `src/lore/server/oidc.py` | Keep but low-priority | OIDC auth works, not actively promoted in V1 |

---

## 12. Phased Implementation

### Phase 0: MVP (Week 1-2) — P0

**Goal:** Ship a working product. `docker compose up` → Claude Desktop remembers things.

#### Week 1: Core Pivot

| Day | Task | Details |
|-----|------|---------|
| 1 | Schema migration | Write `006_openbrain_pivot.sql`, test data migration |
| 1 | Data types | New `Memory`, `SearchResult`, `StoreStats` types |
| 2 | Rename package | `src/lore/` → `src/openbrain/`, update all imports |
| 2 | Update pyproject.toml | New name, entry points, dependencies |
| 3 | MCP server rewrite | 5 new tools (remember, recall, forget, list, stats) |
| 3 | Local store update | SQLite store with new schema |
| 4 | Server store | Extract `ServerStore` class from route handlers |
| 4 | Server-side embedding | Add embedding to server write + search paths |
| 5 | REST API endpoints | New routes: `/v1/memories`, `/v1/memories/search`, `/v1/stats` |

#### Week 2: Polish & Launch

| Day | Task | Details |
|-----|------|---------|
| 1 | Docker updates | New Dockerfile, docker-compose.yml, .env.example |
| 1 | API key prefix | Support `ob_sk_` + `lore_sk_` |
| 2 | Multi-project scoping | Verify project filtering works end-to-end |
| 2 | Test suite | Update all tests for new schema/API |
| 3 | README | New README with quickstart, MCP configs, architecture |
| 3 | MCP config examples | Claude Desktop, Cursor, Windsurf |
| 4 | Integration testing | Full stack: Docker → API → MCP → Claude Desktop |
| 4 | CI/CD | Update GitHub Actions for new package name |
| 5 | Launch prep | Blog post draft, HN submission draft, social posts |

**Exit criteria:**
- [x] `docker compose up` starts Postgres + Open Brain server
- [x] `POST /v1/org/init` creates org and returns `ob_sk_` API key
- [x] `POST /v1/memories` stores a memory with auto-embedding
- [x] `GET /v1/memories/search?q=...` returns semantically relevant results
- [x] MCP server works in Claude Desktop (stdio, remote mode)
- [x] MCP server works in local mode (SQLite, no server needed)
- [x] Existing Lore data migrated to memories table
- [x] Tests pass, CI green

### Phase 1: Ecosystem (Month 2-3) — P1

| Task | Effort | Dependencies |
|------|--------|--------------|
| SSE transport for MCP (FR-010) | 2-3 days | FastMCP SSE support |
| Webhook ingestion endpoint (FR-012) | 1-2 days | Server-side embedding |
| CLI tool (FR-013) | 1-2 days | REST API |
| Python SDK repackage to PyPI (FR-018) | 2-3 days | All API changes stable |
| TypeScript SDK repackage to npm (FR-019) | 2-3 days | All API changes stable |
| TTL / expiration (FR-024) | 1 day | Background cleanup task |
| Cursor/Windsurf setup guides | 1 day | MCP server stable |
| Community Discord | 0.5 days | — |

### Phase 2: Adapters & Polish (Month 4-6) — P2

| Task | Effort | Dependencies |
|------|--------|--------------|
| Slack adapter (FR-020) | 2-3 days | Webhook endpoint |
| Telegram adapter (FR-021) | 2-3 days | Webhook endpoint |
| Redaction pipeline opt-in (FR-023) | 1-2 days | Already exists, needs toggle |
| Memory deduplication (FR-025) | 1-2 days | Embedding pipeline |
| Prometheus metrics | 1 day | — |
| Cloud hosting MVP | 1-2 weeks | Multi-tenant, billing |

### Phase 3: Scale (Month 7-12) — P3

| Task | Effort | Dependencies |
|------|--------|--------------|
| Dashboard UI (FR-022) | 1-2 weeks | REST API stable |
| Multi-user / shared brains | 2-3 weeks | Auth system |
| Memory graph / relationships | 2-3 weeks | Schema extension |
| Configurable embedding models (OpenAI, Cohere) | 1 week | Abstraction layer |
| Enterprise features | TBD | Demand-driven |

---

## Appendix A: Technical Risks & Mitigations

| Risk | Severity | Probability | Mitigation |
|------|----------|-------------|------------|
| **Embedding model dimension lock-in** — 384 dims baked into schema, changing model requires re-embedding all data | HIGH | MEDIUM | V1: accept the constraint. Future: add `embedding_model` column + re-embedding migration script. MiniLM-L6 is good enough for 100K+ memories. |
| **Server-side embedding bottleneck** — every write and search hits ONNX model | MEDIUM | LOW | ~200-400ms is acceptable for V1 volumes. If it becomes an issue: batch embedding, async background embedding, or API-based embedding. |
| **SQLite concurrent access** — local mode with multiple MCP clients writing simultaneously | MEDIUM | LOW | SQLite handles concurrent reads fine. Concurrent writes serialize via file lock. For multi-writer scenarios, use server mode (Postgres). |
| **Docker image size** — ONNX + model makes image large | LOW | HIGH (will be ~400MB) | Acceptable for a dev tool. Can offer "slim" image without embedding model for API-only use in future. |
| **Migration data loss** — bugs in lessons→memories migration | HIGH | LOW | Migration is idempotent, non-destructive (lessons table preserved). Test with production data dump before deploying. |
| **Breaking existing Lore users** — small but non-zero user base | LOW | MEDIUM | Compatibility shim for Python imports. Both API key prefixes accepted. Migration docs. Deprecation warnings, not hard breaks. |
| **FastMCP library instability** — relatively new library | MEDIUM | LOW | Pin version. MCP protocol is stable. If library breaks, can implement protocol directly (it's JSON-RPC over stdio). |

---

## Appendix B: Environment Variables Reference

| Variable | Default | Context | Description |
|----------|---------|---------|-------------|
| `DATABASE_URL` | — | Server | PostgreSQL connection string |
| `OPENBRAIN_STORE` | `local` | MCP/SDK | `local` (SQLite) or `remote` (HTTP) |
| `OPENBRAIN_PROJECT` | — | MCP/SDK/Server | Default project scope |
| `OPENBRAIN_API_URL` | — | MCP/SDK | Server URL (remote mode) |
| `OPENBRAIN_API_KEY` | — | MCP/SDK | API key (remote mode) |
| `OPENBRAIN_REDACT` | `false` | MCP/Server | Enable PII redaction |
| `OPENBRAIN_DB_PATH` | `~/.openbrain/default.db` | MCP/SDK | SQLite path (local mode) |
| `OPENBRAIN_MODEL_DIR` | `~/.openbrain/models` | MCP/SDK/Server | Embedding model cache |
| `OPENBRAIN_PORT` | `8765` | Docker | Server port |
| `POSTGRES_PASSWORD` | `openbrain` | Docker | PostgreSQL password |
| `LOG_LEVEL` | `INFO` | Server | Logging level |
| `LOG_FORMAT` | `pretty` | Server | `pretty` or `json` |
| `AUTH_MODE` | `api-key-only` | Server | `api-key-only`, `dual`, `oidc-required` |
| `METRICS_ENABLED` | `true` | Server | Enable `/metrics` endpoint |
| `RATE_LIMIT_BACKEND` | `memory` | Server | `memory` or `redis` |

---

*This architecture document is designed to be implementable without guessing. When in doubt, refer to the existing Lore codebase — the pivot preserves most of its patterns.*
