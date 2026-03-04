# Epics & Stories — Open Brain

**Author:** Scrum Master (BMAD v6.0.4) | **Date:** 2026-03-03
**Status:** Draft
**Inputs:** [Product Brief](./product-brief.md), [PRD](./prd.md), [Architecture](./architecture.md)
**Developer:** Amit (solo)

---

## Story Summary

| Epic | Stories | P0 | P1 | P2 | P3 |
|------|---------|----|----|----|----|
| 1: Schema Migration & Core | 6 | 6 | — | — | — |
| 2: MCP Server Pivot | 4 | 4 | — | — | — |
| 3: REST API Updates | 4 | 3 | 1 | — | — |
| 4: Rebrand & Documentation | 6 | 5 | — | — | — |
| 5: SDK Updates | 4 | — | 4 | — | — |
| 6: Transport & Ingestion | 3 | — | 3 | — | — |
| 7: Adapters | 3 | — | — | 3 | — |
| 8: Dashboard & Cloud | 2 | — | — | — | 2 |
| Pre-Sprint Tasks | 1 | 1 | — | — | — |
| Backlog (P2) | 1 | — | — | 1 | — |
| **Total** | **34** | **19** | **8** | **4** | **2** |

---

## Epic 1: Schema Migration & Core (P0 — Week 1)

> Migrate from Lore's `lessons` table to the generalized `memories` table. This is the foundation — everything depends on it.

### STORY-001: Create `memories` table migration

- **Epic:** 1 — Schema Migration & Core
- **Priority:** P0
- **Size:** M (1 day)
- **Description:** Write SQL migration `006_openbrain_pivot.sql` that creates the `memories` table with all columns (id, org_id, content, type, source, project, tags, metadata, embedding, created_at, updated_at, expires_at) and all indexes (org, org+project, org+type, org+created_at DESC, GIN on tags, HNSW on embedding).
- **Acceptance Criteria:**
  - Migration file at `migrations/006_openbrain_pivot.sql`
  - Migration is idempotent (uses `IF NOT EXISTS`)
  - HNSW index created with `m=16, ef_construction=64`
  - Migration runs successfully against existing Lore database
  - Migration runs successfully against a fresh database
- **Dependencies:** None
- **Technical Notes:** Schema defined in Architecture doc §4.1. Use `DO $$ ... END $$` block for HNSW index to check existence. Existing orgs/api_keys tables are untouched.

### STORY-002: Migrate existing `lessons` data to `memories`

- **Epic:** 1 — Schema Migration & Core
- **Priority:** P0
- **Size:** S (0.5 day)
- **Description:** Add data migration logic to the migration SQL that copies all rows from `lessons` into `memories`. Combine `problem` + `resolution` into `content`, set type to `'lesson'`, merge context/confidence/votes into `metadata` JSONB.
- **Acceptance Criteria:**
  - All existing lessons are copied to memories table
  - `content` = `problem || '\n\n' || resolution` (or just `problem` if resolution is empty)
  - `type` = `'lesson'` for all migrated rows
  - `metadata` contains `context`, `confidence`, `upvotes`, `downvotes`, `migrated_from: 'lore_lessons'`
  - Uses `ON CONFLICT (id) DO NOTHING` for idempotency
  - `lessons` table is NOT dropped (kept for rollback)
- **Dependencies:** STORY-001
- **Technical Notes:** Part of same migration file as STORY-001. See Architecture §9.1 for exact field mapping. Existing embeddings carry over as-is (same 384-dim vectors).

### STORY-003: Core data types and models

- **Epic:** 1 — Schema Migration & Core
- **Priority:** P0
- **Size:** S (0.5 day)
- **Description:** Create `src/openbrain/types.py` with `Memory`, `SearchResult`, `StoreStats` dataclasses. Create `src/openbrain/server/models.py` with Pydantic models for API request/response (`MemoryCreateRequest`, `MemoryCreateResponse`, `MemoryResponse`, `MemorySearchResult`, `MemorySearchResponse`, `MemoryListResponse`, `StatsResponse`).
- **Acceptance Criteria:**
  - `Memory` dataclass has all fields matching the DB schema
  - `SearchResult` wraps Memory + score
  - `StoreStats` has total_count, count_by_type, count_by_project, oldest/newest dates
  - Pydantic models have proper validation (content min_length=1, limit ranges, etc.)
  - `MemoryCreateRequest` does NOT accept embedding (server generates it)
- **Dependencies:** STORY-001 (schema must be finalized)
- **Technical Notes:** See Architecture §4.3 and §4.4. Types should live in `types.py` at package root; Pydantic models in `server/models.py`.

### STORY-004: ServerStore class (asyncpg)

- **Epic:** 1 — Schema Migration & Core
- **Priority:** P0
- **Size:** M (1-2 days)
- **Description:** Extract storage logic from Lore's route handlers into a dedicated `ServerStore` class at `src/openbrain/server/store.py`. Implements all CRUD operations against the `memories` table using asyncpg. All queries filter by `org_id` for tenant isolation.
- **Acceptance Criteria:**
  - `save()` — inserts memory with ULID, returns ID
  - `get()` — fetches single memory by ID + org_id
  - `search()` — cosine similarity search using pgvector, applies time decay scoring, filters by type/tags/project
  - `list()` — paginated listing with filters, ordered by created_at DESC
  - `delete()` — single delete by ID
  - `delete_by_filter()` — bulk delete with filter combination, returns count
  - `stats()` — aggregate query returning counts by type/project, date range
  - All methods enforce org_id isolation
  - Expired memories (expires_at < now) excluded from search/list/stats
- **Dependencies:** STORY-001, STORY-003
- **Technical Notes:** See Architecture §3.2. Scoring formula: `cosine_similarity × time_decay` where `time_decay = exp(-0.005 × age_days)`. Use parameterized queries (asyncpg does this natively). Connection pool from existing `db.py`.

### STORY-005: Server-side embedding service

- **Epic:** 1 — Schema Migration & Core
- **Priority:** P0
- **Size:** S (0.5 day)
- **Description:** Create `src/openbrain/server/embed.py` — a singleton that loads the ONNX MiniLM-L6-v2 model and provides `embed(text) → List[float]`. Loaded once at server startup (or first call), cached in memory. Used by both REST API writes and search.
- **Acceptance Criteria:**
  - Model loaded once, reused across requests
  - Returns 384-dimension float vector
  - Graceful fallback: if model fails to load, log warning, return None (memories stored without embedding)
  - Embedding latency < 500ms per call
  - Model directory configurable via `OPENBRAIN_MODEL_DIR` env var
- **Dependencies:** None (existing Lore embedding code, just needs extraction)
- **Technical Notes:** Lore already has this in `src/lore/embed/`. Extract and adapt for server context. Key change: Lore's server expected clients to send embeddings; now the server generates them. Initialize in FastAPI lifespan handler.

---

## Epic 2: MCP Server Pivot (P0 — Week 1)

> Replace Lore's 4 MCP tools with Open Brain's 5 tools. This is the headline feature — it's what AI clients interact with.

### STORY-006: MCP `remember` tool

- **Epic:** 2 — MCP Server Pivot
- **Priority:** P0
- **Size:** M (1 day)
- **Description:** Implement the `remember` MCP tool in `src/openbrain/mcp/server.py`. Accepts content (required), type, tags, metadata, project, source. Auto-generates embedding. Returns confirmation with memory ID.
- **Acceptance Criteria:**
  - Tool registered with descriptive name and helpful description (see Architecture §5.1)
  - Description includes WHEN TO USE and WHEN NOT TO USE guidance
  - Content is required; all other fields optional
  - Type defaults to "note" if omitted
  - Project falls back to `OPENBRAIN_PROJECT` env var
  - Returns "✅ Memory saved (ID: ...)" on success
  - Returns "❌ Failed to save: ..." on error
  - Works in both local (SQLite) and remote (HTTP) modes
- **Dependencies:** STORY-003, STORY-005 (for embedding)
- **Technical Notes:** See Architecture §5.1 for full JSON schema. Local mode uses SqliteStore; remote mode uses RemoteStore (HTTP to API). The MCP server should use `MemoryService` as a middle layer to abstract the store.

### STORY-007: MCP `recall` tool

- **Epic:** 2 — MCP Server Pivot
- **Priority:** P0
- **Size:** M (1 day)
- **Description:** Implement the `recall` MCP tool for semantic search. Accepts query (required), type, tags, project, limit. Performs cosine similarity search against stored embeddings.
- **Acceptance Criteria:**
  - Embeds the query text, then searches against stored embeddings
  - Returns ranked results with content, type, tags, metadata, score, ID, created_at
  - Filters by type, tags, project when provided (AND logic)
  - Limit defaults to 5, max 20
  - Results respect org_id isolation
  - Helpful "no results found" message when empty
  - Formatted human-readable output (see Architecture §5.2)
- **Dependencies:** STORY-006 (shares infrastructure), STORY-004 or SqliteStore
- **Technical Notes:** In local mode, cosine similarity is computed client-side (numpy). In remote mode, calls `GET /v1/memories/search?q=...`.

### STORY-008: MCP `forget` and `list` tools

- **Epic:** 2 — MCP Server Pivot
- **Priority:** P0
- **Size:** M (1 day)
- **Description:** Implement `forget` (delete by ID or bulk by filter) and `list` (browse memories, paginated, no semantic search) MCP tools.
- **Acceptance Criteria:**
  - **forget:** Delete by ID returns confirmation or "not found"
  - **forget:** Bulk delete by tags/type/project returns count deleted
  - **forget:** Bulk delete with no filter requires `confirm: true` safety guard
  - **list:** Returns memories ordered by created_at DESC
  - **list:** Supports limit (default 20, max 100) and offset
  - **list:** Filters by type, tags, project
  - **list:** Human-readable formatted output
  - Both tools work in local and remote modes
- **Dependencies:** STORY-006 (shares infrastructure)
- **Technical Notes:** See Architecture §5.3 and §5.4 for schemas and response formats.

### STORY-009: MCP `stats` tool + server registration

- **Epic:** 2 — MCP Server Pivot
- **Priority:** P0
- **Size:** S (0.5 day)
- **Description:** Implement the `stats` tool and finalize MCP server registration. Stats returns total count, count by type/project, date range. Ensure all 5 tools are registered with FastMCP, env var configuration works, and entry points are correct.
- **Acceptance Criteria:**
  - Stats returns: total_count, count_by_type, count_by_project, oldest/newest dates
  - Stats executes in < 500ms for up to 100K memories
  - MCP server starts via `python -m openbrain.mcp` or `openbrain-mcp`
  - All 5 tools registered and discoverable by MCP clients
  - `OPENBRAIN_STORE`, `OPENBRAIN_PROJECT`, `OPENBRAIN_API_URL`, `OPENBRAIN_API_KEY` env vars work
  - Local mode (SQLite) works with zero config
  - Remote mode works with API URL + key
- **Dependencies:** STORY-006, STORY-007, STORY-008
- **Technical Notes:** See Architecture §3.1 for env vars table. Stats query uses COUNT + GROUP BY. Entry point defined in `pyproject.toml` `[project.scripts]`.

---

## Epic 3: REST API Updates (P0 — Week 1)

> Update FastAPI routes to use the new `memories` schema and endpoints. Keep existing auth/middleware/health infrastructure.

### STORY-010: Memory CRUD REST endpoints

- **Epic:** 3 — REST API Updates
- **Priority:** P0
- **Size:** M (1 day)
- **Description:** Create `src/openbrain/server/routes/memories.py` with REST endpoints mirroring MCP tools. `POST /v1/memories` (create), `GET /v1/memories` (list), `GET /v1/memories/{id}` (get one), `DELETE /v1/memories/{id}` (delete one), `DELETE /v1/memories` (bulk delete with filters).
- **Acceptance Criteria:**
  - `POST /v1/memories` — creates memory, auto-embeds, returns 201 + ID
  - `GET /v1/memories` — paginated list with type/tags/project filters
  - `GET /v1/memories/{id}` — returns single memory or 404
  - `DELETE /v1/memories/{id}` — returns 204 or 404
  - `DELETE /v1/memories` — bulk delete with filters, requires `confirm=true`
  - All endpoints require API key auth
  - All endpoints scoped to authenticated org_id
  - OpenAPI/Swagger docs auto-generated
- **Dependencies:** STORY-003, STORY-004, STORY-005
- **Technical Notes:** Replace Lore's `routes/lessons.py`. Use ServerStore + server-side Embedder. Keep existing auth middleware from `auth.py`.

### STORY-011: Search endpoint (server-side embedding)

- **Epic:** 3 — REST API Updates
- **Priority:** P0
- **Size:** S (0.5 day)
- **Description:** Create `GET /v1/memories/search` endpoint. Key change from Lore: the server embeds the query text (previously clients sent pre-computed vectors). Simple GET with `q` parameter.
- **Acceptance Criteria:**
  - `GET /v1/memories/search?q=stripe+rate+limiting` performs semantic search
  - Server embeds the query using the server-side embedder
  - Supports `type`, `tags`, `project`, `limit` query params
  - Returns ranked results with score
  - Returns empty array (not error) when no results found
  - Rate limited (existing middleware)
- **Dependencies:** STORY-004, STORY-005
- **Technical Notes:** See Architecture §6.3. This is the REST equivalent of MCP `recall`. Key architectural change per ADR-006: server embeds, clients don't need to.

### STORY-012: Stats endpoint + API key prefix update

- **Epic:** 3 — REST API Updates
- **Priority:** P0
- **Size:** S (0.5 day)
- **Description:** Add `GET /v1/stats` endpoint and update the auth system to accept both `ob_sk_` and `lore_sk_` API key prefixes. New keys generated with `ob_sk_` prefix.
- **Acceptance Criteria:**
  - `GET /v1/stats` returns StatsResponse (total, by type, by project, date range)
  - Auth accepts `ob_sk_*` AND `lore_sk_*` prefixes
  - `POST /v1/org/init` generates keys with `ob_sk_` prefix
  - Existing `lore_sk_` keys continue to work
  - Health endpoint (`/health`) updated to return openbrain branding
- **Dependencies:** STORY-004
- **Technical Notes:** Per ADR-007, support both prefixes. Auth code does hash-based lookup so prefix is cosmetic — just need to accept both formats in the prefix check.

### STORY-013: Webhook ingestion endpoint

- **Epic:** 3 — REST API Updates
- **Priority:** P1
- **Size:** M (1-2 days)
- **Description:** Create `POST /v1/webhook` endpoint that accepts JSON payloads and stores them as memories. Supports configurable field mapping for different external services.
- **Acceptance Criteria:**
  - `POST /v1/webhook` accepts JSON with `content` field (required)
  - Optional fields: type, tags, metadata, source, project
  - Auto-embeds on receipt
  - Returns 201 with memory ID
  - API key authenticated
  - Rate limited (configurable, default 60/min)
  - Field mapping via config (e.g., map `text` → `content`, `channel` → `source`)
- **Dependencies:** STORY-010, STORY-005
- **Technical Notes:** See Architecture §3.5 and §6.4. Field mapping can be via env var (`OPENBRAIN_WEBHOOK_MAPPINGS` as JSON) or YAML config file. Start simple — direct field mapping, not JSONPath expressions.

---

## Epic 4: Rebrand & Documentation (P0 — Week 1-2)

> Rename everything from Lore to Open Brain. Write the README that sells the product.

### STORY-014: Package rename (Python)

- **Epic:** 4 — Rebrand & Documentation
- **Priority:** P0
- **Size:** M (1-2 days)
- **Description:** Rename `src/lore/` → `src/openbrain/`, update all imports, update `pyproject.toml` (name, packages, entry points), add backward-compatible `lore` shim package with deprecation warning.
- **Acceptance Criteria:**
  - `import openbrain` works
  - `from openbrain import OpenBrain` works
  - `from lore import Lore` works with deprecation warning
  - `pyproject.toml` name is `openbrain`
  - Entry points: `openbrain-mcp`, `openbrain` (CLI)
  - All internal imports use `openbrain.*`
  - All tests pass with new import paths
- **Dependencies:** STORY-003 (types defined first)
- **Technical Notes:** See Architecture §9.2. Use `find ... -exec sed` for bulk import rename. Keep a slim `src/lore/__init__.py` that re-exports from openbrain with warnings.

### STORY-015: Docker & Compose rebrand

- **Epic:** 4 — Rebrand & Documentation
- **Priority:** P0
- **Size:** S (0.5 day)
- **Description:** Update Dockerfile, docker-compose.yml, and .env.example with Open Brain naming. Service names, image names, env vars, database name — all should say openbrain.
- **Acceptance Criteria:**
  - Docker Compose service named `openbrain` (not `lore`)
  - Database name: `openbrain`
  - Database user: `openbrain`
  - `OPENBRAIN_*` env vars (not `LORE_*`)
  - `.env.example` documents all env vars
  - `docker compose up -d` starts full stack successfully
  - Health check passes
  - Multi-stage Dockerfile per Architecture §7.2
- **Dependencies:** STORY-014 (code must be renamed first)
- **Technical Notes:** See Architecture §7.1-7.3. Keep port 8765 as default (avoid breaking existing setups). Postgres image: `pgvector/pgvector:pg16`.

### STORY-016: README rewrite

- **Epic:** 4 — Rebrand & Documentation
- **Priority:** P0
- **Size:** M (1 day)
- **Description:** Write new README.md that sells Open Brain and gets users from zero to working in < 5 minutes. This is the most important marketing asset.
- **Acceptance Criteria:**
  - One-sentence pitch at top: "Give your AI a brain."
  - 3-line quickstart (clone → docker compose up → copy MCP config)
  - Claude Desktop MCP config JSON (copy-paste ready)
  - Cursor and Windsurf config examples
  - Feature list with brief explanations
  - Architecture diagram (ASCII or Mermaid)
  - "Why Open Brain?" section (vs DIY, vs Mem0, vs Zep)
  - REST API quick reference
  - Contributing section
  - MIT License badge
- **Dependencies:** STORY-015 (Docker must work for quickstart to be accurate)
- **Technical Notes:** Write this as if it's a blog post, not API docs. The README IS the landing page. Include shields.io badges for license, Docker pulls, GitHub stars.

### STORY-017: MCP config examples

- **Epic:** 4 — Rebrand & Documentation
- **Priority:** P0
- **Size:** S (0.5 day)
- **Description:** Create ready-to-paste MCP configuration files for Claude Desktop, Cursor, and Windsurf. Both local mode (zero-config) and remote mode (with server).
- **Acceptance Criteria:**
  - `examples/claude_desktop_config.json` — local mode + remote mode
  - `examples/cursor_config.json` — tested with Cursor
  - `examples/windsurf_config.json` — tested with Windsurf
  - Each config is valid JSON, copy-paste ready
  - Configs referenced in README quickstart section
  - Both local and remote mode variants documented
- **Dependencies:** STORY-009 (MCP server must work)
- **Technical Notes:** See Architecture §7.4 for Claude Desktop config. Local mode needs no API key or server URL. Remote mode needs `OPENBRAIN_API_URL` + `OPENBRAIN_API_KEY`.

### STORY-018: CI/CD pipeline update

- **Epic:** 4 — Rebrand & Documentation
- **Priority:** P0
- **Size:** S (0.5 day)
- **Description:** Update GitHub Actions workflows for the new package name. CI should run tests, build Docker image, and (on release) publish to GHCR. PyPI/npm publish can be manual for now.
- **Acceptance Criteria:**
  - `ci.yml` runs tests on push/PR with new import paths
  - `release.yml` builds Docker image tagged `ghcr.io/amitpaz1/openbrain`
  - Multi-arch build: amd64 + arm64
  - Versioned tags: `latest` + semver
  - Tests pass in CI
  - Image size < 500MB
- **Dependencies:** STORY-014, STORY-015
- **Technical Notes:** Update existing Lore workflows. Image registry: GHCR (GitHub Container Registry). Use `docker/build-push-action` with QEMU for multi-arch.

---

## Epic 5: SDK Updates (P1 — Week 2-3)

> Repackage Python and TypeScript SDKs with generalized schema and new package names.

### STORY-019: Python SDK generalization

- **Epic:** 5 — SDK Updates
- **Priority:** P1
- **Size:** M (2 days)
- **Description:** Update the Python SDK (`OpenBrain` class) with new method names (`remember`, `recall`, `forget`, `list`, `stats`) and new data types. The SDK should work in both local mode (embedded SQLite) and remote mode (HTTP to API server).
- **Acceptance Criteria:**
  - `from openbrain import OpenBrain` — main SDK class
  - `ob.remember(content=..., type=..., tags=..., metadata=...)` — stores memory
  - `ob.recall(query=..., type=..., tags=..., limit=...)` — semantic search
  - `ob.forget(id=...)` — delete by ID
  - `ob.list(type=..., tags=..., project=..., limit=..., offset=...)` — browse
  - `ob.stats()` — statistics
  - Local mode works with zero config (SQLite + local embedding)
  - Remote mode works with API URL + key
  - Backward-compatible `Lore` class available with deprecation warning
- **Dependencies:** STORY-010 (REST API for remote mode), STORY-014 (package renamed)
- **Technical Notes:** Mostly a rename + schema change. Existing `src/lore/lore.py` → `src/openbrain/openbrain.py`. Update SqliteStore and RemoteStore interfaces.

### STORY-020: Python SDK publish to PyPI

- **Epic:** 5 — SDK Updates
- **Priority:** P1
- **Size:** S (0.5 day)
- **Description:** Publish the `openbrain` package to PyPI. Publish a deprecation notice on the existing `lore-sdk` package pointing to `openbrain`.
- **Acceptance Criteria:**
  - `pip install openbrain` installs the SDK
  - Package includes: SDK, MCP server, CLI, embedding pipeline
  - `lore-sdk` final version published with deprecation notice in README
  - PyPI page has proper description, links, classifiers
- **Dependencies:** STORY-019
- **Technical Notes:** Use `python -m build` + `twine upload`. Make sure `pyproject.toml` metadata is complete (description, urls, classifiers, license).

### STORY-021: TypeScript SDK generalization

- **Epic:** 5 — SDK Updates
- **Priority:** P1
- **Size:** M (2 days)
- **Description:** Update the TypeScript SDK with new method names and schema. HTTP client only (no local embedding — TS users connect to the server).
- **Acceptance Criteria:**
  - `import { OpenBrain } from 'openbrain'`
  - Methods: `remember()`, `recall()`, `forget()`, `list()`, `stats()`
  - TypeScript types for all request/response models
  - Works with API URL + key configuration
  - Published to npm as `openbrain`
  - `lore-sdk` npm package updated with deprecation notice
- **Dependencies:** STORY-010 (REST API must be stable)
- **Technical Notes:** Lives in `ts/` directory. Update `package.json` name to `openbrain`. Update `src/client.ts` with new endpoints and types.

### STORY-022: CLI tool

- **Epic:** 5 — SDK Updates
- **Priority:** P1
- **Size:** M (1-2 days)
- **Description:** Create CLI entry point for Open Brain. Thin wrapper around the REST API with human-readable output and `--json` flag.
- **Acceptance Criteria:**
  - `openbrain remember "text" --type note --tags infra,deploy`
  - `openbrain recall "search query" --limit 5`
  - `openbrain forget <id>`
  - `openbrain list --type lesson --limit 10`
  - `openbrain stats`
  - Configurable via env vars or `~/.openbrain.yaml`
  - Human-readable output by default
  - `--json` flag for machine-readable output
  - Installed automatically with `pip install openbrain`
- **Dependencies:** STORY-010 (REST API), STORY-014 (entry points)
- **Technical Notes:** See Architecture §3.6. Use `argparse` or `click`. Config resolution: CLI flags → env vars → `~/.openbrain.yaml` → defaults. Entry point: `openbrain` in pyproject.toml.

---

## Epic 6: Transport & Ingestion (P1 — Month 2)

> Add SSE transport for remote MCP connections and TTL support for memory expiration.

### STORY-023: MCP SSE transport

- **Epic:** 6 — Transport & Ingestion
- **Priority:** P1
- **Size:** L (2-3 days)
- **Description:** Add SSE (Server-Sent Events) transport to the MCP server, enabling remote/networked MCP connections from non-local AI clients.
- **Acceptance Criteria:**
  - MCP server can run in SSE mode via `--transport sse` flag or env var
  - SSE endpoint supports API key authentication (Authorization header)
  - Works behind reverse proxy (nginx, Caddy)
  - Compatible with MCP clients that support SSE transport
  - Docker Compose exposes SSE port
  - Reconnection handling for dropped connections
- **Dependencies:** STORY-009 (MCP server stable in stdio mode)
- **Technical Notes:** FastMCP library supports SSE transport. Main work is auth integration, CORS handling, and testing with real MCP clients. Per ADR-005, this was intentionally deferred from V1.

### STORY-024: TTL / expiration support

- **Epic:** 6 — Transport & Ingestion
- **Priority:** P1
- **Size:** M (1 day)
- **Description:** Full TTL support: `remember` accepts `expires_at` or `ttl` parameter. Expired memories are excluded from queries. Background cleanup job deletes expired memories.
- **Acceptance Criteria:**
  - `remember` accepts optional `expires_at` (ISO timestamp) or `ttl` (e.g., "30d", "1h", "7d")
  - Expired memories excluded from `recall`, `list`, `stats` by default
  - Background cleanup runs hourly (configurable interval)
  - `list --include-expired` flag for admin inspection
  - Works in both MCP and REST API interfaces
- **Dependencies:** STORY-001 (expires_at column exists), STORY-006
- **Technical Notes:** `expires_at` column already in schema. Add `WHERE (expires_at IS NULL OR expires_at > now())` to all queries. Background job: use `asyncio` task in FastAPI lifespan, or simple `DELETE FROM memories WHERE expires_at < now()` on a timer.

### STORY-025: Setup guides (Cursor, Windsurf, community)

- **Epic:** 6 — Transport & Ingestion
- **Priority:** P1
- **Size:** S (0.5 day)
- **Description:** Write detailed setup guides for Cursor and Windsurf. Set up community Discord server for support.
- **Acceptance Criteria:**
  - `docs/cursor-setup.md` with step-by-step Cursor integration
  - `docs/windsurf-setup.md` with step-by-step Windsurf integration
  - Discord server created with channels: #general, #help, #showcase, #bugs
  - Discord link in README
  - Guides linked from README
- **Dependencies:** STORY-009, STORY-016
- **Technical Notes:** Test each guide from scratch on a clean machine if possible. Include screenshots where helpful. Discord is free — just create and configure.

---

## Epic 7: Adapters (P2 — Month 3+)

> Platform adapters for automatic memory capture from external services.

### STORY-026: Slack adapter

- **Epic:** 7 — Adapters
- **Priority:** P2
- **Size:** L (2-3 days)
- **Description:** Slack bot that watches configured channels and stores messages as memories via the webhook/REST API.
- **Acceptance Criteria:**
  - Configurable channel whitelist
  - Stores messages with type="conversation", source="slack"
  - Thread messages grouped (thread context in metadata)
  - Configurable triggers: capture all vs. only 📌-reacted messages
  - Runs as sidecar Docker container
  - Setup documentation
- **Dependencies:** STORY-013 (webhook endpoint)
- **Technical Notes:** Use Slack Bolt for Python. Sidecar pattern: separate container in docker-compose, connects to Open Brain via REST API.

### STORY-027: Telegram adapter

- **Epic:** 7 — Adapters
- **Priority:** P2
- **Size:** L (2-3 days)
- **Description:** Telegram bot that captures messages and stores them as memories.
- **Acceptance Criteria:**
  - Bot receives messages in configured chats/groups
  - Stores with type="conversation", source="telegram"
  - `/remember <text>` command for explicit capture
  - Passive mode: capture all messages in designated groups
  - Runs as sidecar Docker container
  - Setup documentation with BotFather instructions
- **Dependencies:** STORY-013 (webhook endpoint)
- **Technical Notes:** Use python-telegram-bot library. Same sidecar pattern as Slack adapter.

### STORY-028: Redaction pipeline (opt-in)

- **Epic:** 7 — Adapters
- **Priority:** P2
- **Size:** M (1 day)
- **Description:** Carry forward Lore's redaction pipeline as an opt-in feature. Default: OFF. Toggle via `OPENBRAIN_REDACT=true`.
- **Acceptance Criteria:**
  - Disabled by default (NOT Lore's default of enabled)
  - `OPENBRAIN_REDACT=true` enables it
  - Scrubs: API keys, passwords, emails, IPs, credit card numbers
  - Redaction happens before embedding generation
  - Original content NOT retained
  - Pattern list configurable via config file
  - Existing Lore tests pass for redaction patterns
- **Dependencies:** STORY-006 (remember tool exists to hook into)
- **Technical Notes:** Lore already has `src/lore/redact/`. Copy to `src/openbrain/redact/`, update the toggle. Main change: default from ON to OFF.

---

## Epic 8: Dashboard & Cloud (P3 — Month 4+)

> Web UI and cloud hosting preparation. Only pursue when demand justifies it.

### STORY-029: Web dashboard UI

- **Epic:** 8 — Dashboard & Cloud
- **Priority:** P3
- **Size:** XL (1-2 weeks)
- **Description:** Simple SPA for browsing, searching, and managing memories. Served by the FastAPI server.
- **Acceptance Criteria:**
  - Browse memories with pagination
  - Semantic and text search
  - Filter by type, tags, project
  - Delete individual memories
  - View memory details including metadata
  - Basic stats overview on landing page
  - Auth via API key
  - Responsive design
- **Dependencies:** STORY-010 (REST API stable)
- **Technical Notes:** Consider a lightweight framework (Preact, htmx+Alpine, or even vanilla JS). Static files served by FastAPI. No separate build step if possible.

### STORY-030: Cloud hosting preparation

- **Epic:** 8 — Dashboard & Cloud
- **Priority:** P3
- **Size:** XL (1-2 weeks)
- **Description:** Multi-tenant cloud infrastructure: billing, signup flow, provisioning. Only pursue when there's demand (100+ self-host users asking for managed option).
- **Acceptance Criteria:**
  - Signup flow with email verification
  - Stripe billing integration (Free, Pro $9/mo, Team $29/mo)
  - Automated provisioning of org + API key
  - Usage metering (memory count per org)
  - Landing page / marketing site
- **Dependencies:** All P0 and P1 stories
- **Technical Notes:** Existing CDK IaC in Lore can be adapted. Consider Supabase or Railway for quick hosting. Don't build this until the product has proven demand.

---

## Sprint Plan

### Sprint 1 — Week 1: Core Migration + MCP Pivot

**Goal:** New schema, new MCP tools, basic REST API. The engine works.

| Story | Title | Size | Priority |
|-------|-------|------|----------|
| STORY-001 | Create `memories` table migration | M | P0 |
| STORY-002 | Migrate existing `lessons` data | S | P0 |
| STORY-003 | Core data types and models | S | P0 |
| STORY-004 | ServerStore class (asyncpg) | M | P0 |
| STORY-005 | Server-side embedding service | S | P0 |
| STORY-006 | MCP `remember` tool | M | P0 |
| STORY-007 | MCP `recall` tool | M | P0 |
| STORY-008 | MCP `forget` and `list` tools | M | P0 |
| STORY-009 | MCP `stats` tool + server registration | S | P0 |
| STORY-010 | Memory CRUD REST endpoints | M | P0 |
| STORY-011 | Search endpoint (server-side embedding) | S | P0 |
| STORY-012 | Stats endpoint + API key prefix | S | P0 |

**Velocity:** ~12 stories, ~8 working days of effort. Tight but feasible — most is renaming/adapting existing code, not greenfield.

**Exit criteria:** MCP tools work end-to-end. REST API serves memories. Embedding works server-side. Data migrated from lessons.

---

### Sprint 2 — Week 2: Rebrand + Docs + Polish

**Goal:** Everything renamed, Docker working, README written, ready to launch.

| Story | Title | Size | Priority |
|-------|-------|------|----------|
| STORY-014 | Package rename (Python) | M | P0 |
| STORY-015 | Docker & Compose rebrand | S | P0 |
| STORY-016 | README rewrite | M | P0 |
| STORY-017 | MCP config examples | S | P0 |
| STORY-018 | CI/CD pipeline update | S | P0 |

**Velocity:** 5 stories, ~4 working days. Leaves buffer for integration testing and launch prep.

**Exit criteria:** `docker compose up` → copy MCP config → Claude Desktop remembers things. README is compelling. CI passes.

---

### Sprint 3 — Week 3-4: SDK + CLI + Launch

**Goal:** SDKs published, CLI working, product launched publicly.

| Story | Title | Size | Priority |
|-------|-------|------|----------|
| STORY-019 | Python SDK generalization | M | P1 |
| STORY-020 | Python SDK publish to PyPI | S | P1 |
| STORY-021 | TypeScript SDK generalization | M | P1 |
| STORY-022 | CLI tool | M | P1 |
| STORY-024 | TTL / expiration support | M | P1 |
| STORY-025 | Setup guides + community | S | P1 |

**Velocity:** 6 stories, ~8 working days across 2 weeks.

**Exit criteria:** `pip install openbrain` works. `npm install openbrain` works. CLI functional. Community Discord live.

---

## Additional Stories (Added from Gate Check Concerns)

### STORY-031: Multi-Project Scoping (FR-007) End-to-End Validation

- **Epic:** 1 — Schema Migration & Core
- **Priority:** P0
- **Size:** S (0.5 day)
- **Description:** Explicit acceptance test ensuring project scoping works end-to-end across MCP tools and REST API. This FR was implicit in other stories but deserves its own explicit validation.
- **Acceptance Criteria:**
  - Memory saved without explicit project uses `OPENBRAIN_PROJECT` env var as default
  - Memory saved with explicit `project: "my-project"` stores that value
  - `recall` with `project: "my-project"` returns only memories from that project
  - `list` with `project: "my-project"` returns only memories from that project
  - `forget` with `project: "my-project"` deletes only memories from that project
  - `stats` with `project: "my-project"` counts only memories from that project
  - Unscoped queries (no project filter) return all projects for the org
  - REST API `/v1/memories/search?project=...` respects project filter
  - Different orgs cannot see each other's projects (org_id isolation verified)
- **Dependencies:** STORY-004, STORY-006, STORY-009
- **Technical Notes:** This is primarily a QA story — test existing functionality explicitly rather than assuming it works. Use a test matrix of project=null, project="default", project="custom" against all tools.

### STORY-032: SQLite Local Store Schema Update

- **Epic:** 1 — Schema Migration & Core
- **Priority:** P0
- **Size:** M (1 day)
- **Description:** Update `SqliteStore` class to work with the new `memories` schema instead of the old `lessons` table. Implement client-side cosine similarity search (using numpy) and time decay scoring.
- **Acceptance Criteria:**
  - `SqliteStore` migrates local SQLite database from `lessons` to `memories` on init
  - All CRUD methods (`save`, `get`, `search`, `list`, `delete`, `stats`) work
  - Client-side embedding search using numpy cosine_similarity
  - Search latency < 1 second for 10K memories (acceptable for local mode)
  - TTL filtering works (excludes expired memories)
  - Local mode (`OPENBRAIN_STORE=sqlite`) works end-to-end with MCP tools
  - Unit tests pass for SQLite storage
- **Dependencies:** STORY-001, STORY-003, STORY-005
- **Technical Notes:** Lore's SqliteStore exists but uses the old schema. Extract cosine_similarity computation logic and adapt to new `memories` table. Time decay scoring is the same: `similarity × exp(-0.005 × age_days)`.

### STORY-033: Trademark & Naming Verification (Pre-Sprint Blocker)

- **Epic:** Administrative (Pre-Sprint)
- **Priority:** P0
- **Size:** S (0.5 day, mostly manual + research)
- **Description:** Verify "Open Brain" is available as a trademark and that key domains/packages are available. This was flagged in PRD Appendix B as a "P0 blocker" — don't invest in branding until this is confirmed.
- **Acceptance Criteria:**
  - USPTO trademark search completed for "Open Brain" (both text and logo if applicable)
  - EU trademark availability checked (if targeting EU users)
  - Domain `openbrain.dev` or `openbrain.ai` available for purchase
  - PyPI: `openbrain` package name not taken
  - npm: `openbrain` package name not taken
  - GitHub: Can create `openbrain` org or repo
  - Docker Hub / GHCR: `openbrain` namespace available
  - Decision documented: proceed with "Open Brain" or pivot to "Engram" (backup name)
- **Dependencies:** None
- **Technical Notes:** Complete this before Sprint 1 kicks off. If "Open Brain" is blocked on any critical channel, pivot to "Engram" and update PRD, brief, and all artifacts. Cost: ~1 hour of research.

### STORY-034: End-to-End Integration Test (First-Run Flow)

- **Epic:** 4 — Rebrand & Documentation
- **Priority:** P0
- **Size:** M (1 day)
- **Description:** Explicit integration test validating the complete first-run user journey from PRD §6: clone → docker compose up → add MCP config → restart Claude Desktop → remember → recall.
- **Acceptance Criteria:**
  - Test runs: `git clone && docker compose up -d && wait for health check`
  - MCP server accessible at stdio interface
  - Create a test memory via `remember` tool with known content
  - Query it back via `recall` tool with semantic search
  - Verify exact match is returned with correct score
  - Verify updated_at timestamp is set
  - Test runs in < 2 minutes end-to-end (including Docker startup)
  - Document any gotchas or manual steps for users
- **Dependencies:** STORY-014 (rename must be done), STORY-015 (Docker must be ready)
- **Technical Notes:** This is a critical path story because it validates that everything integrates. Run it against the exact steps from README to catch any documentation gaps. Consider automating as part of CI/CD.

---

### Additional Concerns Addressed

| Concern | Action | Resolution |
|---------|--------|-----------|
| FR-025 dedup missing | Added STORY-035 to backlog | Dedup story now in P2 backlog with target Month 3+ |
| SQLite update not explicit | Added STORY-032 to Sprint 1 | Now explicit P0 story with acceptance criteria |
| Sprint 1 overloaded | Sprint 1 acceptance noted | 12 stories/8 days in 5 days is aggressive; added buffer note in exit criteria |
| No end-to-end test | Added STORY-034 to Sprint 2 | First-run integration test now explicit in Sprint 2 |
| Trademark blocker | Added STORY-033 as pre-Sprint | Pre-flight check before development starts |
| updated_at semantics | Documented in PRD | Noted that update operation doesn't exist in V1; updated_at always equals created_at unless migration carries previous value |
| Concurrent requests vague | PRD NFR-001 acceptable for V1 | Documented as "pending refinement if cloud is pursued" |
| Product brief open questions | See below | Redaction decision formally closed; other questions documented in architecture decisions |

---

### Decisions Closed (From Product Brief Appendix B)

**Q1: Redaction feature (keep, optional, or remove)?**  
**Closed:** Keep as opt-in. Disabled by default because general memory (notes, snippets) has different privacy needs than operational lessons (which contained sensitive patterns). Can be enabled via `OPENBRAIN_ENABLE_REDACTION=true`.

**Q2: Embedding model (local MiniLM vs API)?**  
**Closed:** Local ONNX MiniLM-L6-v2 (384 dimensions) by default. API-based (e.g., OpenAI) is a future add-on for users who want stronger embeddings.

**Q3: Migration path from Lore users?**  
**Closed:** Lore has 2 GitHub stars; no installed base to migrate. Fresh start with new repo/package names acceptable. Existing Lore users will need to follow migration guide (export→reimport).

**Q4: Mono-repo pivot vs new repo?**  
**Closed:** Pivot `amitpaz1/lore` in-place. Rename package, rebrand Docker, preserve Git history. No community disruption risk (2 stars).

---

### Additional P0 Stories (added from gate check concerns)

| Story | Title | Size | Priority | Sprint |
|-------|-------|------|----------|--------|
| STORY-031 | Multi-project scoping (FR-007) end-to-end validation | S | P0 | Sprint 1 |
| STORY-032 | SQLite local store schema update | M | P0 | Sprint 1 |
| STORY-033 | Trademark / naming check | S | P0 | Pre-Sprint |
| STORY-034 | End-to-end integration test (first-run flow) | M | P0 | Sprint 2 |

### Backlog (P1-P3, prioritized)

| Story | Title | Size | Priority | Target |
|-------|-------|------|----------|--------|
| STORY-013 | Webhook ingestion endpoint | M | P1 | Month 2 |
| STORY-023 | MCP SSE transport | L | P1 | Month 2 |
| STORY-028 | Redaction pipeline (opt-in) | M | P2 | Month 3 |
| STORY-026 | Slack adapter | L | P2 | Month 3+ |
| STORY-027 | Telegram adapter | L | P2 | Month 3+ |
| STORY-035 | Memory deduplication | M | P2 | Backlog |
| STORY-029 | Web dashboard UI | XL | P3 | Month 4+ |
| STORY-030 | Cloud hosting preparation | XL | P3 | Month 4+ |

---

## Critical Path

The longest dependency chain determines the minimum time to MVP:

```
STORY-001 (migration)
  → STORY-002 (data migration)
  → STORY-003 (types)
    → STORY-004 (ServerStore)
      → STORY-010 (REST endpoints)
        → STORY-011 (search endpoint)
    → STORY-006 (remember tool)
      → STORY-007 (recall tool)
        → STORY-008 (forget/list tools)
          → STORY-009 (stats + registration)
            → STORY-017 (MCP configs)
  → STORY-014 (package rename)
    → STORY-015 (Docker rebrand)
      → STORY-016 (README)
        → STORY-018 (CI/CD)
```

**Critical path length:** 7 stories deep (STORY-001 → 003 → 006 → 007 → 008 → 009 → 017)

**Parallelization opportunities:**
- STORY-004 (ServerStore) and STORY-006 (MCP remember) can be done in parallel once types are ready
- STORY-005 (embedding) has no dependencies — can start day 1
- STORY-014 (rename) can start as soon as STORY-003 is done
- REST API stories (010-012) and MCP stories (006-009) are independent tracks

---

## Notes for Amit

1. **Sprint 1 is aggressive** but most stories are adaptation, not creation. The Lore codebase does 80% of the work.
2. **Write the README before polishing code** (per PRD recommendation). The marketing artifact matters more than the last 10% of polish.
3. **Don't forget the blog post.** Draft it during Sprint 2. "I built the product from Nate B Jones' AI memory video" is the headline.
4. **STORY-014 (package rename) is the scariest story** — it touches every file. Do it early in Sprint 2 when you have focus, not Friday afternoon.
5. **The MCP tool descriptions are product copy.** They tell the AI WHEN to use each tool. Write them carefully — they're more important than the README for day-to-day UX.

---

*This document is the implementation plan. Stories are ordered by dependency and sprint. Adjust based on actual velocity after Sprint 1.*
