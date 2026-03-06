# PRD: F8 — Cross-Tool Memory Sharing (Docs + Polish + Integration)

**Feature:** F8
**Version:** v0.6.0 ("Open Brain")
**Status:** Draft
**Author:** John (PM)
**Phase:** 5 — Polish + Positioning
**Depends on:** F1, F2, F3, F4, F5, F6, F7, F9, F10 (all other v0.6.0 features)
**Dependents:** None (final feature)

---

## 1. Problem Statement

Lore v0.6.0 adds 9 new features (F1-F7, F9, F10) that transform it from a simple memory store into a full cognitive memory platform. But features without documentation are invisible. Without clear positioning, setup guides, and integration tests, users can't:

1. **Discover Lore** — The current README doesn't explain how Lore differs from Mem0, Zep, or Cognee. There's no competitive positioning or feature matrix.
2. **Set up Lore** — There are no step-by-step guides for Claude Desktop, Cursor, VS Code, Windsurf, ChatGPT, or Cline. Users must reverse-engineer config from source code.
3. **Trust Lore** — Without integration tests across all 9 features, we can't guarantee the full pipeline (remember -> enrich -> classify -> extract facts -> graph -> recall) works end-to-end.
4. **Evaluate Lore** — No performance benchmarks exist. Users don't know recall latency, enrichment overhead, or consolidation timing.
5. **Migrate to v0.6.0** — Existing v0.5.x users have no guide for schema changes, new fields, and new tools.

This feature is about making everything else we built actually usable and discoverable.

## 2. Goals

1. **README rewrite** — Competitive positioning against Mem0/Zep/Cognee, feature matrix, architecture diagram, quick start.
2. **MCP client setup guides** — Step-by-step for Claude Desktop, Cursor, VS Code, Windsurf, ChatGPT, Cline.
3. **Docker one-liner** — `docker compose up` starts everything (Postgres + pgvector + Lore server).
4. **Migration guide** — v0.5.x to v0.6.0 covering schema changes, new fields, new tools, breaking changes.
5. **Integration tests** — End-to-end tests verifying all 9 features work together.
6. **Performance benchmarks** — Measurable recall latency, enrichment overhead, consolidation timing.
7. **MCP tool descriptions** — Optimized for auto-discovery by AI agents (clear, concise, actionable).
8. **Package metadata** — PyPI/npm descriptions + keywords updated for discoverability.
9. **CHANGELOG** — v0.6.0 changelog with all features, breaking changes, migration notes.
10. **Demo examples** — Sample scripts showing the full cognitive pipeline.
11. **API reference** — All MCP tools, CLI commands, SDK methods documented.
12. **Quick start tutorial** — 5-minute getting started guide.

## 3. Non-Goals

- **New features** — F8 adds zero new functionality. It documents, tests, and polishes what already exists.
- **Docs site / hosted documentation** — A static docs site (e.g., MkDocs, Docusaurus) is out of scope. Markdown files in the repo are sufficient for v0.6.0.
- **Video production** — Demo GIFs or videos are nice-to-have but not required for this PRD.
- **Automated benchmark CI** — Benchmarks are run manually and results documented. Automated benchmark regression tracking is post-v0.6.0.
- **Localization** — English only.

## 4. Design

### 4.1 README Rewrite

The README is the primary marketing surface. It must answer three questions in 30 seconds: What is Lore? Why not Mem0/Zep/Cognee? How do I start?

**Structure:**

```markdown
# Lore — Cross-Agent Memory for AI

One-liner: Persistent semantic memory that works with every MCP-compatible AI tool.

## Why Lore?
- Works locally (SQLite) or at scale (Postgres + pgvector)
- No API key required — local ONNX embeddings, LLM features optional
- Single database — no Neo4j/Redis/Qdrant dependency
- 20 MCP tools for knowledge management
- Knowledge graph, fact extraction, auto-consolidation — all opt-in

## Feature Matrix (vs competitors)
| Feature                  | Lore  | Mem0  | Zep   | Cognee |
|--------------------------|-------|-------|-------|--------|
| Local-first (no server)  | Yes   | No    | No    | No     |
| MCP native               | Yes   | No    | No    | No     |
| Knowledge graph           | Yes   | Yes*  | Yes   | Yes    |
| Fact extraction           | Yes   | No    | No    | Yes    |
| Auto-consolidation        | Yes   | No    | Yes   | No     |
| Conflict resolution       | Yes   | No    | No    | No     |
| Memory tiers              | Yes   | No    | Yes   | No     |
| Dialog classification     | Yes   | No    | No    | No     |
| Webhook ingestion         | Yes   | No    | No    | No     |
| No external DB required   | Yes   | No**  | No    | No     |
| PII masking              | Yes   | No    | No    | No     |

* Mem0 requires Neo4j for graph. ** Mem0 requires Qdrant/Redis.

## Quick Start (5 minutes)
## Architecture
## Setup Guides
## API Reference
## Performance
## Migration from v0.5.x
```

**Competitive positioning principles:**
- Honest comparisons — don't misrepresent competitors. Use footnotes for nuance.
- Lead with differentiators: local-first, MCP-native, single-database, zero API keys.
- Don't trash competitors — position Lore as "the MCP-native choice" rather than "better than X".

### 4.2 MCP Client Setup Guides

One guide per major client. Each guide must include:

1. **Prerequisites** — What to install (Python, uv/pip).
2. **Configuration** — Exact JSON/YAML config for the client's MCP settings file.
3. **Verification** — How to confirm Lore is working (e.g., "ask the agent to remember something").
4. **Troubleshooting** — Common issues (port conflicts, Python version, ONNX installation).

**Clients to cover:**

| Client | Config Location | Format |
|--------|----------------|--------|
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), `%APPDATA%\Claude\claude_desktop_config.json` (Windows) | JSON |
| Cursor | `.cursor/mcp.json` in project root | JSON |
| VS Code (Copilot) | `.vscode/mcp.json` in project root | JSON |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | JSON |
| ChatGPT | Via MCP bridge plugin (document the bridge setup) | JSON |
| Cline | `.cline/mcp_settings.json` in project root | JSON |
| Claude Code | `.claude/settings.json` or CLAUDE.md | JSON |

**Example config (Claude Desktop):**
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

**Remote/Docker config (all clients):**
```json
{
  "mcpServers": {
    "lore": {
      "command": "uvx",
      "args": ["lore-memory"],
      "env": {
        "LORE_STORE": "http://localhost:8765",
        "LORE_PROJECT": "my-project"
      }
    }
  }
}
```

### 4.3 Docker One-Liner Setup

**Goal:** `docker compose up` starts Postgres (with pgvector) + Lore HTTP server. Users connect their MCP client to the server.

**`docker-compose.yml` contents:**

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: lore
      POSTGRES_USER: lore
      POSTGRES_PASSWORD: lore
    ports:
      - "5432:5432"
    volumes:
      - lore-data:/var/lib/postgresql/data

  lore:
    image: ghcr.io/your-org/lore-server:latest
    depends_on:
      - postgres
    environment:
      DATABASE_URL: postgresql://lore:lore@postgres:5432/lore
    ports:
      - "8765:8765"

volumes:
  lore-data:
```

**Deliverables:**
- `docker-compose.yml` at repo root (or `docker/docker-compose.yml` with a symlink)
- Dockerfile for the Lore server (if not already present)
- Health check endpoint for the Lore server (`GET /health`)
- README section explaining `docker compose up` flow

### 4.4 Migration Guide (v0.5.x -> v0.6.0)

**Schema changes to document:**

| Change | Type | Migration |
|--------|------|-----------|
| `tier` column on `memories` | New column | `ALTER TABLE memories ADD COLUMN tier VARCHAR(10) DEFAULT 'long'` |
| `importance_score` column | New column | `ALTER TABLE memories ADD COLUMN importance_score FLOAT DEFAULT 1.0` |
| `access_count` column | New column | `ALTER TABLE memories ADD COLUMN access_count INT DEFAULT 0` |
| `last_accessed_at` column | New column | `ALTER TABLE memories ADD COLUMN last_accessed_at TIMESTAMP` |
| `facts` table | New table | Auto-created on first use |
| `conflict_log` table | New table | Auto-created on first use |
| `entities` table | New table | Auto-created on first use |
| `relationships` table | New table | Auto-created on first use |

**New MCP tools (7 -> 20):**

Document each new tool with a one-line description:
- `as_prompt` — Export memories formatted for LLM context injection
- `check_freshness` — Check if memories are stale based on git activity
- `github_sync` — Sync memories with GitHub issues/PRs
- `classify` — Classify text by intent, domain, and emotion
- `enrich` — Auto-extract metadata (topics, entities, sentiment) from memories
- `extract_facts` — Extract atomic (subject, predicate, object) facts from text
- `list_facts` — List facts extracted from memories
- `conflicts` — View fact conflicts and resolutions
- `graph_query` — Traverse the knowledge graph
- `entity_map` — Get entity relationship map for a topic
- `related` — Find memories connected via graph relationships
- `ingest` — Webhook-style ingestion with source tracking
- `consolidate` — Trigger memory consolidation/summarization

**Breaking changes:**
- `recall()` return format may include additional fields (tier, importance_score, entities). Existing integrations that parse recall output should be tested.
- New `memories` table columns. Auto-migration should handle this, but document manual migration SQL for users who manage their own schema.

**Migration script:**

Provide a `lore migrate` CLI command or automatic migration on startup that:
1. Adds new columns to `memories` table with defaults
2. Creates new tables (`facts`, `conflict_log`, `entities`, `relationships`)
3. Sets all existing memories to `tier='long'`, `importance_score=1.0`, `access_count=0`
4. Is idempotent (safe to run multiple times)

### 4.5 Integration Tests

End-to-end tests verifying the full pipeline works across all features. These are NOT unit tests — they test feature interactions.

**Test scenarios:**

| # | Scenario | Features Tested | Description |
|---|----------|----------------|-------------|
| 1 | Full ingestion pipeline | F4, F5, F6, F9, F2 | `remember()` a complex text. Verify: tier assigned (F4), importance scored (F5), metadata enriched (F6), classified (F9), facts extracted (F2). |
| 2 | Graph-enhanced recall | F1, F2, F6 | Remember multiple related memories. Extract facts/entities. `recall()` with `graph_depth=2`. Verify graph traversal returns connected memories. |
| 3 | Fact conflict lifecycle | F2, F5 | Remember "database is MySQL". Then remember "migrated to PostgreSQL". Verify SUPERSEDE resolution, old fact invalidated, conflict logged. |
| 4 | Consolidation with graph | F3, F1, F4, F5 | Create 10 related short-term memories. Run consolidation. Verify: memories merged, graph edges updated, consolidated memory is long-term tier. |
| 5 | Webhook to recall | F7, F6, F9, F2, F1 | POST to `/ingest` endpoint. Verify: memory stored, enriched, classified, facts extracted, entities added to graph. Then recall the ingested content. |
| 6 | Prompt export with enrichment | F10, F6, F9 | Remember several memories with enrichment. Export via `as_prompt()`. Verify: output includes metadata, is within token budget. |
| 7 | Tier lifecycle | F4, F5, F3 | Create working-tier memory. Verify auto-expiry. Create short-tier memory. Let importance decay. Verify consolidation candidate detection. |
| 8 | Entity map end-to-end | F1, F2, F6 | Remember 5 memories about a project. Call `entity_map()`. Verify: entities extracted, relationships mapped, D3-compatible output format. |
| 9 | Cross-feature recall filters | F4, F9, F1 | Remember memories across tiers and classifications. Recall with tier filter, intent filter, entity filter. Verify correct filtering. |
| 10 | No-LLM mode | All | Disable all LLM features. Run remember/recall/forget/list/stats. Verify: everything works, no LLM calls made, no enrichment/classification/facts. |

**Test infrastructure:**
- Integration tests in `tests/integration/` directory
- Use `MemoryStore` (in-memory) for speed, with a separate suite using `SqliteStore` for schema verification
- LLM calls mocked with deterministic responses for reproducibility
- Test fixtures with realistic multi-memory scenarios

### 4.6 Performance Benchmarks

Measure and document baseline performance for key operations.

**Benchmark scenarios:**

| Operation | What to Measure | Target |
|-----------|----------------|--------|
| `remember()` — no LLM | Latency for store + embed | < 100ms |
| `remember()` — full pipeline | Latency with enrich + classify + extract facts | < 2s (LLM-dependent) |
| `recall()` — vector only | Latency for similarity search (100 memories) | < 50ms |
| `recall()` — vector only | Latency for similarity search (10,000 memories) | < 200ms |
| `recall()` — graph-enhanced | Latency with 2-hop graph traversal | < 500ms |
| `consolidate()` — 50 memories | Time to cluster + summarize | < 10s |
| `as_prompt()` — 100 memories | Time to format + budget | < 100ms |
| `ingest()` — single item | Latency for webhook ingestion | < 2s (with enrichment) |
| Embedding generation | Time to embed a 500-word text | < 200ms (ONNX) |
| Graph query — entity_map | Time for 3-hop traversal (1000 entities) | < 300ms |

**Benchmark method:**
- Python script using `time.perf_counter()` with 10 iterations, report median and p95
- Run on a defined reference system (document specs)
- Results documented in `docs/benchmarks.md`
- Separate SQLite and Postgres results

### 4.7 MCP Tool Descriptions

All 20 MCP tools need descriptions optimized for auto-discovery. AI agents read these descriptions to decide which tool to use. Good descriptions must:

1. **State the purpose** in the first sentence.
2. **Include "USE THIS WHEN"** to help agents know when to invoke the tool.
3. **Be concise** — under 200 characters for the first sentence.
4. **Avoid jargon** — agents may not know Lore-specific terms.

**Review and optimize descriptions for all tools:**

| Tool | Current Status | Action |
|------|---------------|--------|
| `remember` | Existing — needs update for new params (tier, enrichment) | Review and update |
| `recall` | Existing — needs update for graph-enhanced mode | Review and update |
| `forget` | Existing — adequate | Verify |
| `list_memories` | Existing — needs update for new filters | Review and update |
| `stats` | Existing — needs update for graph stats | Review and update |
| `upvote_memory` | Existing — adequate | Verify |
| `downvote_memory` | Existing — adequate | Verify |
| `as_prompt` | New (F10) | Review |
| `check_freshness` | Existing | Verify |
| `github_sync` | Existing | Verify |
| `classify` | New (F9) | Review |
| `enrich` | New (F6) | Review |
| `extract_facts` | New (F2) | Review |
| `list_facts` | New (F2) | Review |
| `conflicts` | New (F2) | Review |
| `graph_query` | New (F1) | Review |
| `entity_map` | New (F1) | Review |
| `related` | New (F1) | Review |
| `ingest` | New (F7) | Review |
| `consolidate` | New (F3) | Review |

**Quality criteria for tool descriptions:**
- An agent with no prior Lore knowledge should be able to pick the right tool for a task
- Descriptions should be self-contained (no "see docs" references)
- Parameter descriptions should explain valid values and defaults

### 4.8 Package Metadata

**PyPI (`pyproject.toml`):**

Update:
- `description` — "Cross-agent semantic memory with knowledge graphs, fact extraction, and MCP integration"
- `keywords` — add: `knowledge-graph`, `fact-extraction`, `memory-consolidation`, `mcp`, `model-context-protocol`, `cognitive-memory`, `ai-memory`, `semantic-memory`, `agent-memory`
- `classifiers` — ensure `Development Status :: 4 - Beta`, relevant `Topic::` classifiers
- `project.urls` — docs link, changelog link

**npm (`package.json` if applicable):**

Update:
- `description`
- `keywords`

### 4.9 CHANGELOG.md

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

```markdown
# Changelog

## [0.6.0] — 2026-03-XX — "Open Brain"

### Added
- **Knowledge Graph (F1):** Entity + relationship extraction, graph traversal,
  `graph_query`, `entity_map`, `related` MCP tools
- **Fact Extraction (F2):** Atomic fact extraction with conflict resolution,
  `extract_facts`, `list_facts`, `conflicts` MCP tools
- **Memory Consolidation (F3):** Auto-summarization of old/similar memories,
  `consolidate` MCP tool
- **Memory Tiers (F4):** Working/short/long-term tiers with tier-specific TTLs
- **Importance Scoring (F5):** Adaptive decay, access tracking, importance-weighted recall
- **Metadata Enrichment (F6):** LLM-powered topic/entity/sentiment extraction,
  `enrich` MCP tool
- **Webhook Ingestion (F7):** REST ingestion endpoint with source adapters,
  `ingest` MCP tool
- **Dialog Classification (F9):** Intent/domain/emotion classification,
  `classify` MCP tool
- **Prompt Export (F10):** Template-based memory export for LLM context,
  `as_prompt` MCP tool
- 13 new MCP tools (7 -> 20 total)
- Docker Compose setup for one-command deployment
- Setup guides for Claude Desktop, Cursor, VS Code, Windsurf, ChatGPT, Cline
- Performance benchmarks documentation

### Changed
- `recall()` now supports graph-enhanced retrieval via `graph_depth` parameter
- `remember()` now supports enrichment pipeline (opt-in via LLM config)
- `list_memories()` supports filtering by tier, classification, entity
- `stats()` includes graph statistics (entity count, relationship count)
- Memory data model extended with `tier`, `importance_score`, `access_count`, `last_accessed_at` fields

### Migration Notes
- New columns added to `memories` table (auto-migrated on startup)
- 4 new tables: `facts`, `conflict_log`, `entities`, `relationships`
- All LLM features are opt-in — existing installations work without changes
- See migration guide in docs/migration-v0.5-to-v0.6.md
```

### 4.10 Demo Examples

Sample scripts in `examples/` directory showing the full cognitive pipeline.

**Example 1: `examples/full_pipeline.py`**

Shows: remember -> enrich -> classify -> extract facts -> graph -> recall

```python
"""
Lore v0.6.0 — Full Cognitive Pipeline Demo

Shows the complete memory lifecycle:
1. Remember a piece of knowledge
2. Auto-enrich with metadata (topics, entities, sentiment)
3. Auto-classify (intent, domain, emotion)
4. Auto-extract facts (subject, predicate, object triples)
5. Build knowledge graph (entities + relationships)
6. Recall with graph-enhanced retrieval
"""
from lore import Lore

lore = Lore(
    llm_provider="anthropic",
    llm_model="claude-haiku-4-5-20251001",
    fact_extraction=True,
    enrichment=True,
    classification=True,
)

# 1. Remember — triggers full pipeline
memory_id = lore.remember(
    "We migrated our database from MySQL 5.7 to PostgreSQL 16 last week. "
    "The team lead Sarah approved the change after benchmarking showed 3x "
    "improvement in query performance.",
    tier="long",
)

# 2. Check what was extracted
facts = lore.get_facts(memory_id)
for f in facts:
    print(f"  ({f.subject}, {f.predicate}, {f.object}) [{f.confidence:.0%}]")

# 3. Recall with graph context
results = lore.recall("What database do we use?", graph_depth=2)
for r in results:
    print(f"  [{r.score:.2f}] {r.memory.content[:80]}...")

# 4. Check entity relationships
entity_map = lore.entity_map("database")
print(f"  Entities: {len(entity_map['entities'])}")
print(f"  Relationships: {len(entity_map['relationships'])}")
```

**Example 2: `examples/mcp_tool_tour.py`**

Shows every MCP tool being used programmatically.

**Example 3: `examples/webhook_ingestion.py`**

Shows setting up webhook ingestion from Slack/Git.

**Example 4: `examples/consolidation_demo.py`**

Shows memory accumulation over time, then consolidation into summaries.

### 4.11 API Reference

Document all public interfaces. Format: one section per interface category.

**MCP Tools (20 tools):**

For each tool, document:
- Name
- Description (the auto-discovery text)
- Parameters (name, type, required/optional, default, description)
- Return format (example output)
- Example usage

**CLI Commands:**

For each command, document:
- Command syntax
- Options/flags
- Example usage
- Example output

**SDK Methods (Lore class):**

For each public method, document:
- Method signature
- Parameters
- Return type
- Example usage

**Location:** `docs/api-reference.md`

### 4.12 Quick Start Tutorial

A 5-minute getting started guide in `docs/quickstart.md` (also embedded in README).

**Structure:**

```
1. Install (30 seconds)
   pip install lore-memory
   # or: uvx lore-memory

2. Configure your AI tool (60 seconds)
   [Link to client-specific guide]

3. Try it (3 minutes)
   - Ask your agent: "Remember that our API uses REST with JSON responses"
   - Ask: "What do you know about our API?"
   - Ask: "What entities are related to our API?"

4. Enable LLM features (optional, 30 seconds)
   Export ANTHROPIC_API_KEY=...
   [Brief config snippet]

5. Next steps
   - [Link to full API reference]
   - [Link to examples]
   - [Link to Docker setup for teams]
```

## 5. File Changes

| File | Change | Type |
|------|--------|------|
| `README.md` | Complete rewrite with competitive positioning, feature matrix, architecture diagram, quick start | Modified |
| `docs/quickstart.md` | 5-minute getting started tutorial | **New** |
| `docs/setup-claude-desktop.md` | Claude Desktop setup guide | **New** |
| `docs/setup-cursor.md` | Cursor setup guide | **New** |
| `docs/setup-vscode.md` | VS Code setup guide | **New** |
| `docs/setup-windsurf.md` | Windsurf setup guide | **New** |
| `docs/setup-chatgpt.md` | ChatGPT (via MCP bridge) setup guide | **New** |
| `docs/setup-cline.md` | Cline setup guide | **New** |
| `docs/setup-claude-code.md` | Claude Code setup guide | **New** |
| `docs/migration-v0.5-to-v0.6.md` | Migration guide | **New** |
| `docs/api-reference.md` | Full API reference (MCP tools, CLI, SDK) | **New** |
| `docs/benchmarks.md` | Performance benchmark results | **New** |
| `docs/architecture.md` | Architecture diagram + explanation | **New** |
| `docker-compose.yml` | Docker Compose for one-command setup | **New** (or update existing) |
| `Dockerfile` | Lore server Dockerfile | **New** (or update existing) |
| `CHANGELOG.md` | v0.6.0 changelog | **New** or Modified |
| `pyproject.toml` | Updated description, keywords, classifiers | Modified |
| `src/lore/mcp/server.py` | Optimized tool descriptions | Modified |
| `examples/full_pipeline.py` | Full cognitive pipeline demo | **New** |
| `examples/mcp_tool_tour.py` | Tour of all MCP tools | **New** |
| `examples/webhook_ingestion.py` | Webhook ingestion demo | **New** |
| `examples/consolidation_demo.py` | Consolidation demo | **New** |
| `tests/integration/test_full_pipeline.py` | Integration test: full ingestion pipeline | **New** |
| `tests/integration/test_graph_recall.py` | Integration test: graph-enhanced recall | **New** |
| `tests/integration/test_fact_conflicts.py` | Integration test: fact conflict lifecycle | **New** |
| `tests/integration/test_consolidation_graph.py` | Integration test: consolidation with graph | **New** |
| `tests/integration/test_webhook_recall.py` | Integration test: webhook to recall | **New** |
| `tests/integration/test_prompt_export.py` | Integration test: prompt export with enrichment | **New** |
| `tests/integration/test_tier_lifecycle.py` | Integration test: tier lifecycle | **New** |
| `tests/integration/test_entity_map.py` | Integration test: entity map end-to-end | **New** |
| `tests/integration/test_cross_feature_filters.py` | Integration test: cross-feature recall filters | **New** |
| `tests/integration/test_no_llm_mode.py` | Integration test: no-LLM baseline | **New** |
| `benchmarks/run_benchmarks.py` | Benchmark runner script | **New** |

## 6. Implementation Plan

### 6.1 Task Breakdown

**Phase A: Integration Tests (highest priority — validates everything works)**

1. Create `tests/integration/` directory and `conftest.py` with shared fixtures
2. Implement integration test scenarios 1-10 (section 4.5)
3. Run all integration tests and fix any cross-feature bugs discovered

**Phase B: Documentation — Core**

4. Write `docs/quickstart.md` (5-minute guide)
5. Write MCP client setup guides (7 guides, one per client)
6. Write `docs/migration-v0.5-to-v0.6.md`
7. Write `docs/api-reference.md`
8. Write `docs/architecture.md` with diagram

**Phase C: README + CHANGELOG**

9. Rewrite `README.md` with competitive positioning, feature matrix, quick start
10. Write `CHANGELOG.md` for v0.6.0

**Phase D: Docker + Package**

11. Create/update `docker-compose.yml` and `Dockerfile`
12. Update `pyproject.toml` metadata (description, keywords, classifiers)

**Phase E: Polish**

13. Review and optimize all 20 MCP tool descriptions in `server.py`
14. Create demo example scripts in `examples/`

**Phase F: Benchmarks**

15. Create benchmark runner script
16. Run benchmarks, document results in `docs/benchmarks.md`

### 6.2 Priority Order Rationale

Integration tests come first because they may reveal cross-feature bugs that need fixing before we document anything. Documentation comes next because it's the primary deliverable. Docker/package metadata are lower priority because they're additive, not blocking.

## 7. Acceptance Criteria

### Must Have (P0)

- [ ] AC-1: README rewritten with competitive positioning, feature matrix showing Lore vs Mem0/Zep/Cognee, and architecture diagram.
- [ ] AC-2: Quick start guide exists and a new user can go from zero to working Lore in 5 minutes following it.
- [ ] AC-3: Setup guides exist for all 7 MCP clients (Claude Desktop, Cursor, VS Code, Windsurf, ChatGPT, Cline, Claude Code) with exact config snippets.
- [ ] AC-4: Docker Compose file works: `docker compose up` starts Postgres + Lore server, MCP client can connect.
- [ ] AC-5: Migration guide covers all schema changes (4 new columns, 4 new tables), all new tools (13 new), and breaking changes.
- [ ] AC-6: Integration tests exist covering all 10 scenarios from section 4.5.
- [ ] AC-7: All integration tests pass.
- [ ] AC-8: All existing tests still pass (no regressions).
- [ ] AC-9: All 20 MCP tool descriptions are reviewed and optimized for auto-discovery.
- [ ] AC-10: CHANGELOG.md documents all v0.6.0 features, changes, and migration notes.
- [ ] AC-11: API reference documents all 20 MCP tools with parameters, return format, and examples.
- [ ] AC-12: API reference documents all CLI commands.
- [ ] AC-13: `pyproject.toml` has updated description and keywords.

### Should Have (P1)

- [ ] AC-14: Performance benchmarks run and documented in `docs/benchmarks.md`.
- [ ] AC-15: Demo example scripts exist in `examples/` (at least `full_pipeline.py`).
- [ ] AC-16: API reference documents SDK methods (Lore class public API).
- [ ] AC-17: Architecture diagram is visual (ASCII art or Mermaid, renderable in GitHub).

### Could Have (P2)

- [ ] AC-18: Demo GIF in README showing the full pipeline.
- [ ] AC-19: Benchmark CI that runs on every release.
- [ ] AC-20: npm package.json updated (if TypeScript SDK exists).

## 8. Success Metrics

| Metric | Target |
|--------|--------|
| All existing tests pass | 100% |
| All integration tests pass | 100% (10 scenarios) |
| New integration test count | >= 30 tests |
| MCP client setup guides | 7 guides, all verified working |
| Docker compose up to working | < 60 seconds |
| Quick start completion time | < 5 minutes for a new user |
| README competitive matrix | Covers at least 3 competitors |
| MCP tool descriptions | All 20 reviewed and optimized |
| CHANGELOG completeness | All 9 features documented |

## 9. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Integration tests reveal cross-feature bugs | High — delays release | Budget time for bug fixes. Integration tests are Phase A precisely to catch issues early. |
| MCP client configs change between versions | Medium — guides become stale | Document the config format principles, not just exact JSON. Link to each client's MCP docs. |
| Docker image not published to registry | Medium — `docker compose up` fails | Provide `docker compose build` instructions as fallback. Publish image as part of release. |
| Competitive comparison becomes outdated | Low — competitors evolve | Date the comparison. Focus on architectural differences (local-first, single-DB) that are structural, not feature races. |
| Benchmark results vary by hardware | Low — misleading numbers | Document test hardware specs. Report relative comparisons (e.g., "graph recall adds ~50ms over vector-only"). |
| ChatGPT MCP support is experimental | Medium — guide may not work | Clearly mark as "experimental" in the guide. Prioritize Claude Desktop, Cursor, VS Code guides. |

## 10. Interaction with All Features

F8 is unique — it touches every other feature without modifying their code.

| Feature | F8 Interaction |
|---------|---------------|
| F1 (Knowledge Graph) | Document graph_query, entity_map, related tools. Integration test for graph-enhanced recall. Benchmark graph traversal. |
| F2 (Fact Extraction) | Document extract_facts, list_facts, conflicts tools. Integration test for conflict lifecycle. |
| F3 (Consolidation) | Document consolidate tool. Integration test for consolidation with graph. Benchmark consolidation timing. |
| F4 (Memory Tiers) | Document tier parameter in remember. Integration test for tier lifecycle. |
| F5 (Importance Scoring) | Document importance_score in recall results. Integration test for importance decay. |
| F6 (Metadata Enrichment) | Document enrich tool. Integration test for enrichment pipeline. Benchmark enrichment overhead. |
| F7 (Webhook Ingestion) | Document ingest tool. Integration test for webhook-to-recall. |
| F9 (Dialog Classification) | Document classify tool. Integration test for classification filters. |
| F10 (Prompt Export) | Document as_prompt tool. Integration test for prompt export with enrichment. |

## 11. Future Considerations (Out of Scope)

- **Hosted documentation site** — MkDocs/Docusaurus with search, versioning. Post-v0.6.0.
- **Interactive API playground** — Web UI for testing MCP tools. Post-v0.6.0.
- **Multi-language SDKs** — TypeScript/Go/Rust SDK documentation. Post-v0.6.0.
- **Automated compatibility testing** — CI that tests against every MCP client version. Post-v0.6.0.
- **Performance regression CI** — Automated benchmarks on every PR. Post-v0.6.0.
- **Contributor guide** — How to add new features, store backends, enrichment steps. Post-v0.6.0.
