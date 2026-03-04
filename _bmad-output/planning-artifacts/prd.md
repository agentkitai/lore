# Product Requirements Document — Open Brain

**Author:** John (Product Manager, BMAD v6.0.4) | **Date:** 2026-03-03
**Status:** Draft
**Input:** [Product Brief — Open Brain](./product-brief.md)
**Project:** Open Brain (pivot from Lore)

---

## 1. Executive Summary

Open Brain is an open-source, self-hosted memory layer for AI systems. It exposes five MCP tools — `remember`, `recall`, `forget`, `list`, `stats` — backed by Postgres + pgvector, enabling any MCP-compatible AI (Claude Desktop, Cursor, Windsurf, custom agents) to persist and retrieve knowledge across sessions. Deployed via `docker compose up` for ~$0.10-0.30/month, it targets the gap between "I know I need AI memory" (90K+ views on Nate B Jones' tutorial) and "I have AI memory working" — shipping the turnkey product that video's audience is looking for.

---

## 2. Vision & Goals

### Vision
Every AI deserves persistent memory. Open Brain makes that a one-command setup.

### Goals

| # | Goal | Measurable Target | Timeframe |
|---|------|-------------------|-----------|
| G1 | Ship MCP-native memory server | P0 FRs complete, Docker image published | Week 2 |
| G2 | Capture Nate B Jones distribution wave | 200 GitHub stars, HN front page attempt | Month 1 |
| G3 | Establish as default MCP memory tool | 1,000 stars, 200 active installs | Month 3 |
| G4 | Validate cloud monetization | 100 cloud users, $2K MRR | Month 6 |

### Non-Goals (V1)
- Cloud hosting / managed service
- Dashboard UI
- Multi-user / team features
- Platform adapters (Slack, Telegram)
- Enterprise features (SSO, audit logs, RBAC)

---

## 3. Target Users

### Persona 1: The MCP Power User (Primary — Launch Target)

- **Who:** Developer using Claude Desktop, Cursor, or Windsurf daily
- **Context:** Has seen the Nate B Jones video or similar. Technical enough for `docker compose up` but doesn't want to build from scratch.
- **Pain:** "I explained my project to Claude yesterday. Today it forgot everything."
- **Success:** Copy MCP config into Claude Desktop, run Docker, AI remembers things across sessions within 10 minutes.
- **WTP:** $0-10/mo (will self-host eagerly)

### Persona 2: The AI Agent Builder (Secondary)

- **Who:** Building agents with LangChain, CrewAI, AutoGen, or custom frameworks
- **Pain:** "Every agent run starts from zero. I need persistent state."
- **Success:** Agent stores and retrieves knowledge via MCP or REST API programmatically.
- **WTP:** $20-50/mo for managed

### Persona 3: The AI-Native Startup (Tertiary — Phase 2+)

- **Who:** Building products where AI memory is a core feature
- **Pain:** "We need multi-tenant memory infrastructure we don't have to build."
- **Success:** API-driven multi-tenant memory with project scoping.
- **WTP:** $100-500/mo

### Exclusions
- Enterprise knowledge management buyers
- RAG-only / document retrieval teams
- Non-technical users (no Docker experience)
- Users who need a GUI as primary interface (V1 is headless)

---

## 4. Functional Requirements

### 4.1 Core MCP Tools (P0)

#### FR-001: `remember` Tool
- **Priority:** P0
- **Description:** MCP tool to store a memory. Accepts `content` (required), `type` (optional, default "note"), `tags` (optional), `metadata` (optional JSON), `project` (optional namespace), `source` (optional). Auto-generates embedding on write.
- **Acceptance Criteria:**
  - Calling `remember` with content string stores a row in `memories` table
  - Embedding is generated automatically (MiniLM-L6, 384 dimensions)
  - Returns confirmation with memory ID (ULID)
  - Type defaults to "note" if omitted
  - Tags stored as JSONB array, metadata as JSONB object
  - Project scoping inherited from env var `OPENBRAIN_PROJECT` if not passed
- **Dependencies:** FR-006 (schema), FR-008 (embedding pipeline)

#### FR-002: `recall` Tool
- **Priority:** P0
- **Description:** MCP tool for semantic search across memories. Accepts `query` (required), `tags` (optional filter), `type` (optional filter), `project` (optional filter), `limit` (optional, default 5, max 20).
- **Acceptance Criteria:**
  - Performs cosine similarity search against stored embeddings
  - Returns ranked results with content, type, tags, metadata, score, ID, created_at
  - Filters by tags, type, and project when provided (AND logic)
  - Results respect org_id isolation
  - Returns helpful message when no results found
- **Dependencies:** FR-006, FR-008

#### FR-003: `forget` Tool
- **Priority:** P0
- **Description:** MCP tool to delete a specific memory by ID, or bulk-delete by filter (tags, type, project, or "all" with confirmation).
- **Acceptance Criteria:**
  - Delete by ID: removes single memory, returns confirmation
  - Delete by filter: accepts tags/type/project combo, returns count of deleted
  - Bulk delete (no filter) requires explicit `confirm: true` parameter
  - Returns error if ID not found
  - Soft-delete not required for V1 (hard delete is fine)
- **Dependencies:** FR-006

#### FR-004: `list` Tool
- **Priority:** P0
- **Description:** MCP tool to list/browse memories without semantic search. Supports pagination, filtering by type/tags/project, and sorting by created_at.
- **Acceptance Criteria:**
  - Returns memories ordered by created_at descending (default)
  - Supports `limit` (default 20, max 100) and `offset` for pagination
  - Filters by type, tags, project (AND logic)
  - Returns content, type, tags, metadata, ID, created_at for each
  - Does NOT require or use embeddings
- **Dependencies:** FR-006

#### FR-005: `stats` Tool
- **Priority:** P0
- **Description:** MCP tool returning summary statistics about the memory store.
- **Acceptance Criteria:**
  - Returns: total memory count, count by type, count by project, oldest/newest memory dates, storage size estimate
  - Executes in < 500ms for stores up to 100K memories
  - Scoped to current org_id
- **Dependencies:** FR-006

### 4.2 Schema & Storage (P0)

#### FR-006: Generalized Memory Schema
- **Priority:** P0
- **Description:** Migrate from Lore's `lessons` table (problem/resolution) to generalized `memories` table (content/type/metadata). Must be a clean SQL migration from existing schema.
- **Acceptance Criteria:**
  - New `memories` table with columns: `id` (TEXT, ULID), `content` (TEXT, NOT NULL), `type` (TEXT, default 'note'), `source` (TEXT), `project` (TEXT), `tags` (JSONB), `metadata` (JSONB), `embedding` (vector(384)), `created_at` (TIMESTAMPTZ), `updated_at` (TIMESTAMPTZ), `expires_at` (TIMESTAMPTZ), `org_id` (TEXT, FK to orgs)
  - Migration script converts existing `lessons` data: `problem + "\n\n" + resolution` → `content`, type set to "lesson", context → metadata.context
  - HNSW index on embedding column
  - Indexes on org_id, (org_id, project)
  - Backward-compatible: existing orgs/api_keys tables unchanged
- **Dependencies:** None

#### FR-007: Multi-Project Scoping
- **Priority:** P0
- **Description:** Memories are scoped by project namespace. When `OPENBRAIN_PROJECT` is set or `project` is passed to a tool, all operations filter by that project. When omitted, operations span all projects within the org.
- **Acceptance Criteria:**
  - Project is a free-text string, not a separate table
  - All five MCP tools respect project scoping
  - Unscoped queries return results from all projects
  - Project is set per-tool-call or globally via env var
- **Dependencies:** FR-006

### 4.3 Embedding Pipeline (P0)

#### FR-008: Auto-Embedding on Write
- **Priority:** P0
- **Description:** Every memory stored via `remember` (or webhook/API) gets an embedding generated automatically. Uses local MiniLM-L6 model by default (no external API dependency).
- **Acceptance Criteria:**
  - Embedding generated synchronously on write (acceptable for V1)
  - Uses sentence-transformers MiniLM-L6-v2 (384 dimensions) — already in Lore
  - Model loaded once, cached in memory
  - Graceful fallback: if embedding fails, memory is still stored (embedding = NULL), logged as warning
  - Future: configurable to use OpenAI/Cohere embedding APIs (not in V1)
- **Dependencies:** None (existing Lore capability)

### 4.4 MCP Server & Transport (P0/P1)

#### FR-009: MCP Server (stdio transport)
- **Priority:** P0
- **Description:** MCP server exposing all five tools over stdio transport, compatible with Claude Desktop, Cursor, and other MCP clients.
- **Acceptance Criteria:**
  - Server starts via `python -m openbrain.mcp` or `openbrain-mcp` entry point
  - Registers all five tools with descriptive names and helpful descriptions
  - Tool descriptions include WHEN to use and WHEN NOT to use guidance
  - Configurable via env vars: `OPENBRAIN_STORE` (local/remote), `OPENBRAIN_PROJECT`, `OPENBRAIN_API_URL`, `OPENBRAIN_API_KEY`
  - Works with Claude Desktop MCP config (JSON snippet in README)
- **Dependencies:** FR-001 through FR-005

#### FR-010: SSE Transport for MCP
- **Priority:** P1
- **Description:** Add SSE (Server-Sent Events) transport to MCP server, enabling remote/networked MCP connections (required for cloud and non-local deployments).
- **Acceptance Criteria:**
  - MCP server can run in SSE mode via flag or env var
  - Supports authentication (API key in header)
  - Works behind reverse proxy (nginx, Caddy)
  - Compatible with MCP clients that support SSE transport
  - Docker Compose exposes SSE port alongside REST API
- **Dependencies:** FR-009

### 4.5 REST API (P0)

#### FR-011: REST API Endpoints
- **Priority:** P0
- **Description:** FastAPI REST endpoints mirroring MCP tool functionality. These already largely exist in Lore — need renaming and schema updates.
- **Acceptance Criteria:**
  - `POST /v1/memories` — create memory (= remember)
  - `GET /v1/memories/search?q=...` — semantic search (= recall)
  - `GET /v1/memories` — list with filters (= list)
  - `DELETE /v1/memories/{id}` — delete (= forget)
  - `GET /v1/stats` — statistics (= stats)
  - All endpoints require API key authentication (existing Lore auth)
  - OpenAPI/Swagger docs auto-generated
  - Rate limiting preserved from Lore
- **Dependencies:** FR-006

### 4.6 Webhook Ingestion (P1)

#### FR-012: Webhook Ingestion Endpoint
- **Priority:** P1
- **Description:** A generic POST endpoint that accepts arbitrary payloads and stores them as memories. Enables external services to push data into Open Brain without MCP.
- **Acceptance Criteria:**
  - `POST /v1/webhook` accepts JSON body with at minimum a `content` field
  - Optional fields: `type`, `tags`, `metadata`, `source`, `project`
  - Auto-embeds on receipt
  - Supports configurable field mapping (e.g., map `text` → `content`, `channel` → `source`) via config, NOT code changes
  - Returns 201 with memory ID
  - API key authenticated
  - Rate limited (configurable, default 60/min)
- **Dependencies:** FR-006, FR-008

### 4.7 CLI Tool (P1)

#### FR-013: CLI Client
- **Priority:** P1
- **Description:** Command-line tool for interacting with Open Brain from a terminal. Thin wrapper around the REST API.
- **Acceptance Criteria:**
  - `openbrain remember "deployment uses port 8080" --type note --tags infra`
  - `openbrain recall "what port does deployment use"`
  - `openbrain forget <id>`
  - `openbrain list --type lesson --limit 10`
  - `openbrain stats`
  - Configurable via env vars or `~/.openbrain.yaml`
  - Installable via `pip install openbrain` (ships with SDK)
  - Output: human-readable by default, `--json` flag for machine output
- **Dependencies:** FR-011

### 4.8 Docker & Deployment (P0)

#### FR-014: Docker Compose One-Command Deploy
- **Priority:** P0
- **Description:** Single `docker compose up` starts Postgres + pgvector and Open Brain server. Must work on amd64 and arm64.
- **Acceptance Criteria:**
  - `docker compose up -d` starts full stack
  - Includes Postgres with pgvector extension
  - Includes Open Brain server (FastAPI + MCP)
  - Default API key generated on first run and printed to stdout
  - Health check endpoint (`/health`) included
  - Persistent volume for Postgres data
  - Configurable via `.env` file (port, project, embedding model)
  - Works on amd64 AND arm64 (Apple Silicon, Raspberry Pi)
  - Total startup time < 60 seconds
- **Dependencies:** FR-006, FR-011

#### FR-015: Docker Image on Registry
- **Priority:** P0
- **Description:** Published Docker image on GHCR (GitHub Container Registry) for easy pulling.
- **Acceptance Criteria:**
  - Image published to `ghcr.io/amitpaz1/openbrain` (or equivalent)
  - Multi-arch: amd64 + arm64
  - Versioned tags (latest + semver)
  - Image size < 500MB
  - CI/CD pipeline auto-publishes on release
- **Dependencies:** FR-014

### 4.9 Documentation & Branding (P0)

#### FR-016: README & Quickstart
- **Priority:** P0
- **Description:** New README that sells the product and gets users from zero to working in < 5 minutes.
- **Acceptance Criteria:**
  - One-sentence pitch at top: "Give your AI a brain."
  - 3-line quickstart (clone, docker compose up, copy MCP config)
  - Claude Desktop MCP config JSON snippet (copy-paste ready)
  - Cursor / Windsurf config examples
  - Feature list with brief explanations
  - Architecture diagram (from product brief)
  - "Why Open Brain?" section (vs DIY, vs Mem0, vs Zep)
  - Contributing guide link
  - License (MIT)
- **Dependencies:** None

#### FR-017: MCP Config Snippet
- **Priority:** P0
- **Description:** Ready-to-paste MCP client configuration for major AI tools.
- **Acceptance Criteria:**
  - Claude Desktop config (JSON for `claude_desktop_config.json`)
  - Cursor config
  - Windsurf config
  - Each tested and verified working
  - Included in README and as standalone files in repo
- **Dependencies:** FR-009

### 4.10 SDK Updates (P1)

#### FR-018: Python SDK (Repackaged)
- **Priority:** P1
- **Description:** Existing Lore Python SDK repackaged as `openbrain` on PyPI with generalized schema.
- **Acceptance Criteria:**
  - `pip install openbrain` installs SDK
  - Core methods: `remember()`, `recall()`, `forget()`, `list()`, `stats()`
  - Works with both local (embedded Postgres) and remote (API) backends
  - Backward-compatible Lore import path available (deprecation notice)
  - Published to PyPI
- **Dependencies:** FR-006, FR-011

#### FR-019: TypeScript SDK (Repackaged)
- **Priority:** P1
- **Description:** Existing Lore TypeScript SDK repackaged as `openbrain` on npm.
- **Acceptance Criteria:**
  - `npm install openbrain` installs SDK
  - Core methods mirror Python SDK
  - Published to npm
- **Dependencies:** FR-006, FR-011

### 4.11 Adapters (P2)

#### FR-020: Slack Adapter
- **Priority:** P2
- **Description:** Slack bot/webhook that captures messages and stores them as memories.
- **Acceptance Criteria:**
  - Configurable to watch specific channels
  - Stores messages as memories with type="conversation", source="slack"
  - Respects thread context (groups thread messages)
  - Configurable filters (keywords, reactions like 📌 trigger capture)
  - Runs as sidecar Docker container
- **Dependencies:** FR-011, FR-012

#### FR-021: Telegram Adapter
- **Priority:** P2
- **Description:** Telegram bot that captures messages and stores them as memories.
- **Acceptance Criteria:**
  - Bot receives messages in configured chats/groups
  - Stores as memories with type="conversation", source="telegram"
  - Command-based: `/remember <text>` for explicit capture
  - Passive mode: capture all messages in designated groups
  - Runs as sidecar Docker container
- **Dependencies:** FR-011, FR-012

### 4.12 Dashboard UI (P3)

#### FR-022: Web Dashboard
- **Priority:** P3
- **Description:** Simple web UI for browsing, searching, and managing memories.
- **Acceptance Criteria:**
  - Browse memories with pagination
  - Search (semantic and text)
  - Filter by type, tags, project
  - Delete individual memories
  - View memory details including metadata
  - Basic stats overview
  - Runs as static SPA served by the FastAPI server
  - Auth via API key
- **Dependencies:** FR-011

### 4.13 Redaction (P2)

#### FR-023: Optional Redaction Pipeline
- **Priority:** P2
- **Description:** Carry forward Lore's redaction capability as an opt-in feature. Scrubs PII/secrets before storage.
- **Acceptance Criteria:**
  - Disabled by default (behavior change from Lore)
  - Enabled via `OPENBRAIN_REDACT=true` env var
  - Redacts: API keys, passwords, emails, IPs, credit card numbers (Lore's existing patterns)
  - Redaction happens before embedding generation
  - Redacted content stored; original is NOT retained
  - Configurable pattern list via config file
- **Dependencies:** FR-001, existing Lore redaction code

### 4.14 Memory Management (P1/P2)

#### FR-024: TTL / Expiration
- **Priority:** P1
- **Description:** Memories can have an optional expiration time. Expired memories are excluded from queries and periodically cleaned up.
- **Acceptance Criteria:**
  - `remember` accepts optional `expires_at` (ISO timestamp) or `ttl` (duration string like "30d", "1h")
  - Expired memories excluded from `recall`, `list`, `stats`
  - Background cleanup job runs hourly (configurable) to hard-delete expired memories
  - `list` has `--include-expired` flag for admin inspection
- **Dependencies:** FR-006

#### FR-025: Memory Deduplication
- **Priority:** P2
- **Description:** Detect and prevent storing near-duplicate memories.
- **Acceptance Criteria:**
  - On `remember`, compute similarity against existing memories
  - If similarity > 0.95 (configurable threshold), reject with "similar memory exists" message and return existing memory ID
  - Can be disabled via `OPENBRAIN_DEDUP=false`
  - Does not apply to different types (a "note" and a "lesson" with similar content are both kept)
- **Dependencies:** FR-008

---

## 5. Non-Functional Requirements

### NFR-001: Performance
| Metric | Target | Notes |
|--------|--------|-------|
| `remember` latency | < 500ms (including embedding) | Embedding is the bottleneck (~200-400ms for MiniLM-L6) |
| `recall` latency (1K memories) | < 200ms | HNSW index handles this |
| `recall` latency (100K memories) | < 500ms | May need index tuning |
| `list` latency | < 100ms | Simple SQL query |
| `stats` latency | < 500ms | Aggregate query |
| Concurrent MCP connections | 1 (stdio is single-client) | SSE/REST handles concurrency |
| REST API concurrent requests | 50+ | FastAPI async handlers |

### NFR-002: Security
- API key authentication on all REST endpoints (existing Lore implementation)
- MCP stdio transport: no auth needed (local process, inherits OS user permissions)
- MCP SSE transport: API key in Authorization header
- No plaintext secrets in Docker Compose defaults
- API keys hashed in database (existing)
- SQL injection prevention via parameterized queries (existing)
- Rate limiting on all API endpoints (existing)
- Optional redaction pipeline for PII (FR-023)

### NFR-003: Reliability
- Graceful degradation: if embedding model fails to load, server still starts (memories stored without embeddings, search degraded to text-match)
- Database connection retry with exponential backoff on startup
- Health check endpoint for monitoring
- Docker restart policy: `unless-stopped`

### NFR-004: Deployment
- Single Docker Compose file for full stack
- Total deployment resources: < 512MB RAM, < 1 CPU core for 10K memories
- Persistent storage via Docker volume
- Works behind reverse proxy (X-Forwarded-For, configurable base path)
- ARM64 support (Raspberry Pi, Apple Silicon)
- No external service dependencies (embedding model runs locally)

### NFR-005: Data
- Postgres as single data store (no Redis, no external caches)
- Backups: standard `pg_dump` — documented in README
- No data telemetry / phone-home (zero external calls)
- All data stays on user's infrastructure

### NFR-006: Observability
- Structured JSON logging (existing Lore logging)
- Log levels configurable via env var
- `/health` endpoint returns uptime, memory count, DB connection status
- `/metrics` endpoint (Prometheus-compatible) — P2, not V1

---

## 6. User Journeys

### Journey 1: First-Time Setup (MCP Power User)

```
1. User finds Open Brain (via Nate B Jones reference, HN, GitHub)
2. Reads README → sees 3-line quickstart
3. git clone && docker compose up -d           (2 minutes)
4. Copies Claude Desktop MCP config from README (1 minute)
5. Restarts Claude Desktop
6. Says to Claude: "Remember that my API runs on port 8080"
7. Claude calls `remember` → memory stored
8. Next day, asks Claude: "What port does my API use?"
9. Claude calls `recall` → gets the memory → answers "port 8080"
10. User realizes it works → keeps using it
```

**Total time to value: < 10 minutes**

### Journey 2: Agent Builder Integration

```
1. Developer building a CrewAI agent pipeline
2. Installs openbrain Python SDK: pip install openbrain
3. Configures with API URL + key
4. Agent saves decisions: ob.remember("chose React over Vue for speed", type="decision", project="webapp")
5. Future agent run: ob.recall("frontend framework choice") → gets the decision
6. Agent pipeline has persistent memory across runs
```

### Journey 3: Webhook Capture (Phase 2)

```
1. Developer wants to capture CI/CD events as memories
2. Configures GitHub Actions webhook → Open Brain webhook endpoint
3. POST /v1/webhook with deployment info
4. AI can later recall: "when was the last deployment?"
5. Memory includes metadata: commit SHA, environment, status
```

### Journey 4: CLI Quick Capture

```
1. Developer debugging in terminal, finds a tricky fix
2. openbrain remember "pg_stat_statements needs shared_preload_libraries, not just CREATE EXTENSION" --type lesson --tags postgres
3. Later, different project: openbrain recall "postgres extension not loading"
4. Gets the lesson back → saves 30 minutes of debugging
```

---

## 7. Success Metrics

### Launch (Month 1)
| Metric | Target | Measurement |
|--------|--------|-------------|
| GitHub stars | 200+ | GitHub API |
| Docker pulls | 100+ | GHCR/Docker Hub stats |
| MCP config shares (estimated) | 50+ | Proxy: README views, install count |
| Hacker News front page | 1 attempt | Manual tracking |
| Setup time for new user | < 10 minutes | Manual testing |

### Traction (Month 3)
| Metric | Target | Measurement |
|--------|--------|-------------|
| GitHub stars | 1,000+ | GitHub API |
| Weekly active installs | 200+ | Docker pulls delta / opt-in ping |
| Community Discord members | 100+ | Discord stats |
| External blog/video mentions | 10+ | Search / alerts |
| Contributors (non-Amit) | 5+ | GitHub |

### Product-Market Fit (Month 6)
| Metric | Target | Measurement |
|--------|--------|-------------|
| GitHub stars | 2,500+ | GitHub API |
| Weekly active installs | 500+ | Docker pulls delta |
| Cloud users | 100+ | Cloud DB |
| MRR | $2,000+ | Stripe |
| 30-day retention | 40%+ | Opt-in telemetry or cloud data |

### North Star Metric
**Weekly active memories created across all installs.**

### Anti-Metrics (Signals Something Is Wrong)
- Stars up, Docker pulls flat → hype but no usage
- Installs up, memories/week flat → setup works but product isn't useful
- Cloud signups but no usage → pricing/value mismatch
- High `forget` rate → users storing junk, recall quality is poor

---

## 8. Scope & Constraints

### In Scope (V1 — P0)
- Generalized memory schema (content/type/metadata)
- Five MCP tools: remember, recall, forget, list, stats
- MCP server (stdio transport)
- REST API endpoints
- Docker Compose deployment
- Docker image on GHCR
- README with quickstart + MCP configs
- Multi-project scoping
- Auto-embedding (local MiniLM-L6)
- API key authentication
- Multi-org support (existing)

### In Scope (V1.x — P1, Month 2-3)
- SSE transport for MCP
- Webhook ingestion endpoint
- CLI tool
- Python SDK repackage
- TypeScript SDK repackage
- TTL / expiration
- Cursor / Windsurf setup guides

### Out of Scope (V1)
- Cloud hosting / managed service
- Dashboard / web UI
- Slack / Telegram adapters
- Memory deduplication
- Redaction pipeline (exists but disabled, not promoted)
- Multi-user / team features
- Memory graph / relationships
- Auto-summarization / pruning agents
- Enterprise features (SSO, RBAC, audit log)
- Prometheus metrics endpoint
- Configurable embedding models (OpenAI, Cohere)
- Mobile app

### Constraints
- **Solo developer** — all scope must be achievable by one person
- **No marketing budget** — distribution is content-driven (blog, HN, Reddit, X)
- **Existing codebase** — must migrate from Lore, not rewrite from scratch
- **MIT license** — all V1 code is open source, no proprietary components
- **No external API dependencies** — embedding model runs locally, no OpenAI calls required
- **Backward compatibility** — existing Lore users (however few) should have a migration path

---

## 9. Dependencies & Risks

### Technical Dependencies

| Dependency | Risk | Mitigation |
|------------|------|------------|
| **Postgres + pgvector** | LOW — mature, widely deployed | Already working in Lore |
| **sentence-transformers (MiniLM-L6)** | LOW — stable, well-maintained | Already working in Lore; pin version |
| **FastMCP library** | MEDIUM — relatively new, Anthropic-maintained | Pin version; MCP protocol is stable even if library changes |
| **Docker** | LOW — industry standard | Already working in Lore |
| **Python 3.10+** | LOW — mainstream | Already in use |

### Business Risks

| Risk | Severity | Probability | Mitigation |
|------|----------|-------------|------------|
| **Platform-native memory** — Anthropic/OpenAI build persistent memory into their products | CRITICAL | MEDIUM (12-18mo window) | Move fast. Position as cross-platform/portable alternative. Platform memory won't be open or portable. |
| **Someone else ships first** — Another dev builds the same "Nate B Jones product" | HIGH | MEDIUM | Ship in 1-2 weeks. First mover with quality wins. Monitor r/LocalLLaMA and HN for competitors. |
| **Distribution failure** — Great product, nobody finds it | HIGH | HIGH | Nate B Jones angle is primary distribution hack. Multiple launch channels (HN, Reddit, X, YouTube). Write the blog post before writing the code. |
| **Solo dev burnout** — Can't sustain OSS + cloud + community alone | HIGH | HIGH | Keep scope minimal. Don't launch cloud until demand justifies it. Accept that community support will be slow. |
| **Name collision** — "Open Brain" is trademarked or domain unavailable | MEDIUM | MEDIUM | Check before committing. "Engram" is backup name. Don't do a big launch before trademark clearance. |
| **MCP adoption stalls** | LOW | LOW | REST API provides fallback. MCP has broad industry backing. |
| **Lore → Open Brain migration breaks things** | MEDIUM | LOW | Migration is well-scoped (schema change + rename). Test thoroughly. Keep Lore package available with deprecation notice. |

### PM Challenge: The Distribution Problem

I want to call this out explicitly because it's the #1 risk that isn't a technical problem.

The product brief is honest: Lore got 2 GitHub stars. The technology was solid. Distribution was the failure. Open Brain has a better market hook (Nate B Jones, MCP wave, flexible schema), but **the same solo developer with the same zero marketing budget is shipping it.**

**My recommendation:** Write the launch blog post and HN submission BEFORE finishing the code. The marketing artifact is more important than the last 10% of polish. Ship something good at 90% with great distribution, rather than something perfect at 100% that nobody sees.

---

## 10. Phased Delivery Plan

### Phase 0: MVP (Week 1-2) — P0 Items

**Goal:** Ship a working, installable, documented product.

| Week | Deliverable | FRs | Effort |
|------|------------|-----|--------|
| 1 | Schema migration (lessons → memories) | FR-006 | 1-2 days |
| 1 | MCP tool rename + generalize (5 tools) | FR-001–005, FR-009 | 2-3 days |
| 1 | REST API endpoint updates | FR-011 | 1 day |
| 2 | Docker image + Compose update | FR-014, FR-015 | 1 day |
| 2 | README + MCP configs + quickstart | FR-016, FR-017 | 1-2 days |
| 2 | Multi-project scoping | FR-007 | 0.5 days |
| 2 | Testing + polish + launch prep | — | 1-2 days |

**Exit criteria:** `docker compose up` → copy MCP config → Claude Desktop remembers things. Blog post drafted.

### Phase 1: Ecosystem (Month 2-3) — P1 Items

| Deliverable | FRs | Effort |
|------------|-----|--------|
| SSE transport for MCP | FR-010 | 2-3 days |
| Webhook ingestion | FR-012 | 1-2 days |
| CLI tool | FR-013 | 1-2 days |
| Python SDK repackage (PyPI) | FR-018 | 2-3 days |
| TypeScript SDK repackage (npm) | FR-019 | 2-3 days |
| TTL / expiration | FR-024 | 1 day |
| Community Discord setup | — | 0.5 days |
| Cursor/Windsurf guides | — | 1 day |

**Exit criteria:** 1,000 stars, 200 active installs, SDK published, CLI working.

### Phase 2: Adapters & Polish (Month 4-6) — P2 Items

| Deliverable | FRs | Effort |
|------------|-----|--------|
| Slack adapter | FR-020 | 2-3 days |
| Telegram adapter | FR-021 | 2-3 days |
| Redaction pipeline (opt-in) | FR-023 | 1-2 days |
| Memory deduplication | FR-025 | 1-2 days |
| Cloud hosting MVP | — | 1-2 weeks |
| Prometheus metrics | — | 1 day |

**Exit criteria:** Adapters working, cloud beta, dedup live.

### Phase 3: Scale (Month 7-12) — P3 Items

| Deliverable | FRs | Effort |
|------------|-----|--------|
| Dashboard UI | FR-022 | 1-2 weeks |
| Multi-user / shared brains | — | 2-3 weeks |
| Memory graph / relationships | — | 2-3 weeks |
| Enterprise features | — | Scoped later |

**Exit criteria:** 2,500 stars, 500 installs, $2K MRR.

---

## Appendix A: FR Summary by Priority

| Priority | Count | FRs |
|----------|-------|-----|
| **P0** | 14 | FR-001 through FR-009, FR-011, FR-014 through FR-017 |
| **P1** | 6 | FR-010, FR-012, FR-013, FR-018, FR-019, FR-024 |
| **P2** | 4 | FR-020, FR-021, FR-023, FR-025 |
| **P3** | 1 | FR-022 |
| **Total** | **25** | |

### P0 Breakdown (Must ship in Week 1-2)
- 5 MCP tools (remember, recall, forget, list, stats)
- Schema migration
- Multi-project scoping
- Embedding pipeline (existing)
- MCP server (stdio)
- REST API updates
- Docker Compose + image
- README + MCP configs

### P1 Breakdown (Month 2-3)
- SSE transport
- Webhook ingestion
- CLI tool
- Python + TypeScript SDKs
- TTL/expiration

---

## Appendix B: Naming & Branding Checklist (Pre-Launch Blocker)

Before public launch, verify:
- [ ] "Open Brain" trademark search (USPTO, EU)
- [ ] Domain: openbrain.dev or openbrain.ai
- [ ] PyPI package: `openbrain`
- [ ] npm package: `openbrain`
- [ ] GitHub org or repo name: `openbrain`
- [ ] Docker Hub / GHCR namespace
- [ ] If any blocked: pivot to "Engram" as backup

**This is a P0 blocker.** Don't invest in branding/content until name is clear.

---

## Appendix C: Schema Migration Reference

### Current (Lore)
```sql
lessons (
    id, org_id, problem, resolution, context,
    tags, confidence, source, project, embedding,
    created_at, updated_at, expires_at,
    upvotes, downvotes, meta
)
```

### Target (Open Brain)
```sql
memories (
    id, org_id, content, type, source,
    project, tags, metadata, embedding,
    created_at, updated_at, expires_at
)
```

### Migration logic
- `content` = `problem || '\n\n' || resolution`
- `type` = 'lesson'
- `metadata` = `meta` merged with `{"context": context, "confidence": confidence, "upvotes": upvotes, "downvotes": downvotes}`
- `tags`, `source`, `project`, `embedding`, timestamps carried over
- `confidence`, `upvotes`, `downvotes` dropped as first-class columns (moved to metadata)

---

## Appendix D: Clarifications from Gate Check

### Concurrent Requests Definition (NFR-001)

**Clarification:** "50+ concurrent requests" means up to 50 simultaneous HTTP requests from different clients hitting the REST API simultaneously. Response times should not degrade by more than 2x compared to single-request baseline under this load. This is verified through load testing in Sprint 2. Exact SLA will be refined if cloud hosting (P3) materializes.

### `updated_at` Column Semantics

**Clarification:** The `updated_at` column exists in the schema but V1 has no update operation. To modify a memory, users must:
1. `forget` (delete) the memory by ID
2. `remember` (recreate) it with new content

Therefore, for all newly created memories, `updated_at` == `created_at`. This is a deliberate simplification for V1. Future versions may add a "update memory" MCP tool and REST endpoint, but it's out of scope for launch. This non-feature is documented in Sprint 1 acceptance criteria.

### Redaction Feature Decision

**Closed:** Redaction pipeline (PII stripping) is implemented as **opt-in, disabled by default**. Rationale: Lore's redaction was designed for operational lessons (code patterns, API limits) where sensitive data is common. Open Brain's general-purpose memory (notes, conversations, snippets) has different trust models. Enable via `OPENBRAIN_ENABLE_REDACTION=true` env var. Redaction itself is FR-023 (P2).

---

*This PRD is a living document. Update as requirements are refined during implementation.*
